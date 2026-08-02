import json
import logging
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

def _resolve_bot_credentials(app_id: str | None = None) -> tuple[str, str]:
    if app_id and settings.GAME_FEISHU_APP_ID and app_id == settings.GAME_FEISHU_APP_ID:
        return settings.GAME_FEISHU_APP_ID, settings.GAME_FEISHU_APP_SECRET
    return settings.FEISHU_APP_ID, settings.FEISHU_APP_SECRET


async def _get_tenant_token(app_id: str | None = None, app_secret: str | None = None) -> str:
    """Get Feishu tenant_access_token for specific bot credentials."""
    target_id, target_secret = app_id, app_secret
    if not target_id or not target_secret:
        target_id, target_secret = _resolve_bot_credentials(app_id)

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": target_id,
                "app_secret": target_secret,
            },
        )
        data = resp.json()
        return data.get("tenant_access_token", "")


async def upload_image(image_bytes: bytes, app_id: str | None = None) -> str | None:
    """Upload image bytes to Feishu Open Platform and return image_key."""
    token = await _get_tenant_token(app_id=app_id)
    async with httpx.AsyncClient(timeout=30.0) as client:
        files = {
            "image_type": (None, "message"),
            "image": ("chart.png", image_bytes, "image/png"),
        }
        resp = await client.post(
            "https://open.feishu.cn/open-apis/im/v1/images",
            headers={"Authorization": f"Bearer {token}"},
            files=files,
        )
        resp_json = resp.json()
        if resp.status_code == 200 and resp_json.get("code") == 0:
            image_key = resp_json.get("data", {}).get("image_key")
            logger.info("Successfully uploaded image to Feishu: image_key=%s", image_key)
            return image_key
        else:
            logger.error("Failed to upload image to Feishu: status=%d, resp=%s", resp.status_code, resp_json)
            return None


def translate_spec_fields(obj: dict, sql: str | None = None) -> dict:
    """Recursively translate English field names in Vega spec to Chinese."""
    import copy
    import re
    obj = copy.deepcopy(obj)
    
    # 1. Extract actual data keys and partition them by type (nominal vs quantitative)
    row_keys = []
    nominal_keys = []
    quantitative_keys = []
    
    values = obj.get("data", {}).get("values", [])
    if isinstance(values, list) and len(values) > 0 and isinstance(values[0], dict):
        first_row = values[0]
        row_keys = list(first_row.keys())
        for k, val in first_row.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                quantitative_keys.append(k)
            else:
                nominal_keys.append(k)

    # 2. Extract mappings from SQL aliases (e.g. `browser AS 浏览器`)
    sql_mappings = {}
    if sql and isinstance(sql, str):
        # Strip comments
        cleaned_sql = re.sub(r"--.*", "", sql)
        cleaned_sql = re.sub(r"/\*.*?\*/", "", cleaned_sql, flags=re.DOTALL)
        # Find matches for identifier/expression AS alias
        matches = re.findall(r"([\w_\.]+)\s+AS\s+([\u4e00-\u9fa5\w_]+)", cleaned_sql, re.IGNORECASE)
        for eng, chn in matches:
            eng_key = eng.split(".")[-1].lower() # strip table prefix
            sql_mappings[eng_key] = chn

    # Base translation dictionary for standard business concepts as strong fallback
    FIELD_TRANSLATIONS = {
        # Dimensions
        "brand": "品牌",
        "category": "品类",
        "browser": "浏览器",
        "user_type": "用户类型",
        "operating_system": "操作系统",
        "os": "操作系统",
        "device_category": "设备类型",
        "device": "设备",
        "country": "国家",
        "region": "地区",
        "city": "城市",
        "event_date": "日期",
        "created_at": "日期",
        "date": "日期",
        "register_channel": "注册渠道",
        "channel": "渠道",
        "status": "订单状态",
        "product_name": "商品名称",
        "cohort_month": "注册月份",
        "event_name": "事件名称",
        "user_pseudo_id": "用户ID",
        "level_id": "关卡ID",
        "level": "关卡",
        
        # Metrics
        "total_items_sold": "售出总件数",
        "total_sales_amount": "总销售额",
        "sales_amount": "销售额",
        "available_inventory": "可售库存",
        "avg_age": "平均库龄",
        "sale_price": "销售价格",
        "total_orders": "订单总数",
        "orders_count": "订单数",
        "order_count": "订单数",
        "user_count": "用户数",
        "active_users": "活跃用户数",
        "dau": "DAU",
        "mau": "MAU",
        "retention_rate": "留存率",
        "first_purchase_users": "首购用户数",
        "avg_order_amount": "首单平均商品金额",
        "avg_amount": "平均金额",
        "conversion_rate": "转化率",
        
        # Funnel steps
        "homepage_visits": "首页访问数",
        "category_visits": "分类页访问数",
        "product_visits": "商品页访问数",
        "cart_visits": "购物车访问数",
        "purchase_visits": "购买数",
        "homepage": "首页访问数",
        "category_page": "分类页访问数",
        "product_page": "商品页访问数",
        "cart": "购物车访问数",
        "purchase": "购买事件数",
        "buy": "购买数"
    }

    # Gather all fields in the Vega-Lite encoding that need mapping
    encoding_fields = []
    
    def _collect_fields(item):
        if isinstance(item, dict):
            for k, v in item.items():
                if k == "field" and isinstance(v, str):
                    encoding_fields.append(v)
                else:
                    _collect_fields(v)
        elif isinstance(item, list):
            for x in item:
                _collect_fields(x)

    _collect_fields(obj.get("encoding", {}))
    # De-duplicate while preserving order
    unique_enc_fields = []
    for f in encoding_fields:
        if f not in unique_enc_fields:
            unique_enc_fields.append(f)

    # Pre-calculate mappings for all unique encoding fields in the spec
    final_mappings = {}
    mapped_rk = set()

    for f in unique_enc_fields:
        mapped_to = None
        
        # Rule 1: SQL AS alias match
        if f.lower() in sql_mappings:
            alias = sql_mappings[f.lower()]
            if alias in row_keys:
                mapped_to = alias
                
        # Rule 2: Exact or Case-Insensitive FIELD_TRANSLATIONS dictionary mapping
        if not mapped_to:
            translated = FIELD_TRANSLATIONS.get(f) or FIELD_TRANSLATIONS.get(f.lower())
            if translated:
                if translated in row_keys:
                    mapped_to = translated
                else:
                    # Fuzzy match: check if any row key contains or is contained by the translation
                    for rk in row_keys:
                        if translated in rk or rk in translated:
                            mapped_to = rk
                            break
                    if not mapped_to:
                        mapped_to = translated

        # Rule 3: Already matches a row key directly
        if not mapped_to and f in row_keys:
            mapped_to = f

        # Rule 4: Dynamic semantic substring heuristic
        if not mapped_to:
            for rk in row_keys:
                if "homepage" in f.lower() and "首页" in rk:
                    mapped_to = rk
                    break
                if "category" in f.lower() and "分类" in rk:
                    mapped_to = rk
                    break
                if "product" in f.lower() and "商品" in rk:
                    mapped_to = rk
                    break
                if "cart" in f.lower() and "购物车" in rk:
                    mapped_to = rk
                    break
                if ("purchase" in f.lower() or "buy" in f.lower()) and ("购买" in rk or "支付" in rk or "订单" in rk):
                    mapped_to = rk
                    break
                if "browser" in f.lower() and "浏览器" in rk:
                    mapped_to = rk
                    break
                if "user" in f.lower() and "用户" in rk:
                    mapped_to = rk
                    break
                if "channel" in f.lower() and "渠道" in rk:
                    mapped_to = rk
                    break
                if "rate" in f.lower() and "率" in rk:
                    mapped_to = rk
                    break

        if mapped_to:
            final_mappings[f] = mapped_to
            mapped_rk.add(mapped_to)

    # Rule 5: Positional data-type index alignment fallback for any remaining unmapped fields
    unmapped_fields = [f for f in unique_enc_fields if f not in final_mappings]
    if unmapped_fields and row_keys:
        rem_nominal_rk = [rk for rk in nominal_keys if rk not in mapped_rk]
        rem_quantitative_rk = [rk for rk in quantitative_keys if rk not in mapped_rk]
        
        for f in unmapped_fields:
            # Determine type (default is quantitative)
            f_type = "quantitative"
            enc = obj.get("encoding", {})
            for enc_ch in enc.values():
                if isinstance(enc_ch, dict) and enc_ch.get("field") == f:
                    f_type = str(enc_ch.get("type", "quantitative")).lower()
                    break
            
            if f_type in ("nominal", "ordinal") and rem_nominal_rk:
                aligned = rem_nominal_rk.pop(0)
                final_mappings[f] = aligned
                mapped_rk.add(aligned)
            elif rem_quantitative_rk:
                aligned = rem_quantitative_rk.pop(0)
                final_mappings[f] = aligned
                mapped_rk.add(aligned)
            elif rem_nominal_rk:
                aligned = rem_nominal_rk.pop(0)
                final_mappings[f] = aligned
                mapped_rk.add(aligned)

    # 3. Recursively rewrite the Vega spec with aligned mappings
    def _recurse(item):
        if isinstance(item, dict):
            new_dict = {}
            for k, v in item.items():
                if k == "values":
                    new_dict[k] = v
                elif k == "field" and isinstance(v, str):
                    new_dict[k] = final_mappings.get(v, v)
                else:
                    new_dict[k] = _recurse(v)
            return new_dict
        elif isinstance(item, list):
            return [_recurse(x) for x in item]
        else:
            return item

    return _recurse(obj)


def vega_to_vchart(spec: dict, sql: str | None = None) -> dict | None:
    """Convert Vega-Lite JSON spec into Feishu VChart spec dictionary.

    Returns VChart spec dict if converted successfully, or None for fallback.
    """
    if not isinstance(spec, dict):
        return None
    
    # Translate fields in Vega spec dynamically to match data rows
    spec = translate_spec_fields(spec, sql=sql)

    if any(key in spec for key in ("hconcat", "vconcat", "layer")):
        return None

    try:
        raw_title = spec.get("title")
        title_text = ""
        if isinstance(raw_title, str):
            title_text = raw_title
        elif isinstance(raw_title, dict):
            title_text = raw_title.get("text", "")

        title_cfg = {"text": title_text, "visible": bool(title_text)}

        values = spec.get("data", {}).get("values", [])
        if not values:
            return None

        data_spec = [{"id": "data", "values": values}]

        mark = spec.get("mark")
        mark_type = mark.get("type", mark) if isinstance(mark, dict) else mark

        # 1. Bar Chart
        if mark_type in ["bar", "rect"]:
            enc = spec.get("encoding", {})
            x_field = enc.get("x", {}).get("field")
            y_field = enc.get("y", {}).get("field")
            color_field = enc.get("color", {}).get("field")
            if not x_field or not y_field:
                return None
            
            # Detect horizontal bar charts
            x_type = str(enc.get("x", {}).get("type", "")).lower()
            y_type = str(enc.get("y", {}).get("type", "")).lower()
            is_horizontal = (x_type == "quantitative" or y_type in ["nominal", "ordinal"])
            
            vchart = {
                "type": "bar",
                "title": title_cfg,
                "data": data_spec,
                "xField": x_field,
                "yField": y_field,
            }
            if is_horizontal:
                vchart["direction"] = "horizontal"
                
            if color_field:
                vchart["seriesField"] = color_field
                vchart["stack"] = False
            return vchart

        # 1.5. Area Chart
        elif mark_type == "area":
            enc = spec.get("encoding", {})
            x_field = enc.get("x", {}).get("field")
            y_field = enc.get("y", {}).get("field")
            color_field = enc.get("color", {}).get("field")
            if not x_field or not y_field:
                return None
            vchart = {
                "type": "area",
                "title": title_cfg,
                "data": data_spec,
                "xField": x_field,
                "yField": y_field,
            }
            if color_field:
                vchart["seriesField"] = color_field
            return vchart

        # 2. Line Chart
        elif mark_type in ["line", "trail"]:
            enc = spec.get("encoding", {})
            x_field = enc.get("x", {}).get("field")
            y_field = enc.get("y", {}).get("field")
            color_field = enc.get("color", {}).get("field")
            if not x_field or not y_field:
                return None
            vchart = {
                "type": "line",
                "title": title_cfg,
                "data": data_spec,
                "xField": x_field,
                "yField": y_field,
            }
            if color_field:
                vchart["seriesField"] = color_field
            return vchart

        # 3. Pie / Arc / Donut Chart
        elif mark_type in ["arc", "pie"]:
            enc = spec.get("encoding", {})
            val_field = enc.get("theta", {}).get("field") or enc.get("y", {}).get("field")
            cat_field = enc.get("color", {}).get("field") or enc.get("x", {}).get("field")
            if not val_field or not cat_field:
                return None
            
            # Read innerRadius from mark dictionary or title to support Ring/Donut charts dynamically
            inner_val = 0
            if isinstance(mark, dict):
                inner_val = mark.get("innerRadius", 0)
                if inner_val > 1:
                    inner_val = 0.5
            if "环形" in title_text or "donut" in title_text.lower() or "ring" in title_text.lower():
                inner_val = 0.5

            return {
                "type": "pie",
                "title": title_cfg,
                "data": data_spec,
                "valueField": val_field,
                "categoryField": cat_field,
                "outerRadius": 0.8,
                "innerRadius": inner_val,
                "label": {
                    "visible": True
                },
                "legend": {
                    "visible": True,
                    "orient": "bottom"
                }
            }

        return None
    except Exception as e:
        logger.warning("Failed to translate Vega spec to VChart spec: %s", e)
        return None


def _optimize_vega_spec(spec: dict) -> dict:
    """Optimize Vega-Lite spec for card rendering (e.g. non-zero scale for ratios/margins)."""
    if not isinstance(spec, dict):
        return spec
    import copy
    spec = copy.deepcopy(spec)

    if "config" not in spec:
        spec["config"] = {}
    if isinstance(spec["config"], dict):
        spec["config"]["background"] = "#ffffff"

    specs_to_process = []
    if "hconcat" in spec and isinstance(spec["hconcat"], list):
        specs_to_process.extend(spec["hconcat"])
    elif "vconcat" in spec and isinstance(spec["vconcat"], list):
        specs_to_process.extend(spec["vconcat"])
    elif "layer" in spec and isinstance(spec["layer"], list):
        specs_to_process.extend(spec["layer"])
    else:
        specs_to_process.append(spec)

    for sub in specs_to_process:
        if isinstance(sub, dict) and "encoding" in sub and isinstance(sub["encoding"], dict):
            enc = sub["encoding"]
            if "y" in enc and isinstance(enc["y"], dict):
                y_enc = enc["y"]
                field_name = str(y_enc.get("field", ""))
                axis_fmt = str(y_enc.get("axis", {}).get("format", "")) if isinstance(y_enc.get("axis"), dict) else ""
                if "率" in field_name or "占比" in field_name or "%" in axis_fmt or "ratio" in field_name.lower():
                    if "scale" not in y_enc:
                        y_enc["scale"] = {"zero": False}

    return spec


async def send_text_message(chat_id: str, text: str, app_id: str | None = None) -> dict:
  """Send plain text message."""
  token = await _get_tenant_token(app_id=app_id)
  receive_id_type = "open_id" if chat_id.startswith("ou_") else "chat_id"
  payload = {
      "receive_id": chat_id,
      "msg_type": "text",
      "content": json.dumps({"text": text}),
  }
  logger.info("Sending Feishu message to %s (type=%s), text length=%d", chat_id, receive_id_type, len(text))
  async with httpx.AsyncClient(timeout=20.0) as client:
      resp = await client.post(
          "https://open.feishu.cn/open-apis/im/v1/messages",
          params={"receive_id_type": receive_id_type},
          headers={"Authorization": f"Bearer {token}"},
          json=payload,
      )
      resp_json = resp.json()
      if resp.status_code != 200 or resp_json.get("code") != 0:
          logger.error("Feishu API error! Status: %d, Response: %s", resp.status_code, json.dumps(resp_json, ensure_ascii=False))
      else:
          logger.info("Feishu message sent successfully.")
      return resp_json


async def send_result_card(chat_id: str, summary: str, result_url: str, app_id: str | None = None) -> dict:
  """Send result card with summary and detail link."""
  token = await _get_tenant_token(app_id=app_id)
  receive_id_type = "open_id" if chat_id.startswith("ou_") else "chat_id"
  card_content = {
      "elements": [
          {"tag": "div", "text": {"tag": "lark_md", "content": summary}},
          {"tag": "action", "actions": [
              {"tag": "button", "text": {"tag": "plain_text", "content": "查看详情"},
               "url": result_url, "type": "primary"}
          ]},
      ]
  }
  async with httpx.AsyncClient() as client:
      resp = await client.post(
          "https://open.feishu.cn/open-apis/im/v1/messages",
          params={"receive_id_type": receive_id_type},
          headers={"Authorization": f"Bearer {token}"},
          json={
              "receive_id": chat_id,
              "msg_type": "interactive",
              "content": json.dumps(card_content),
          },
      )
      return resp.json()


async def send_premium_result_card(chat_id: str, question: str, result, cleaned_summary: str, result_url: str | None = None, app_id: str | None = None) -> dict:
    """Send high-quality interactive message card (lark v2 card)."""
    token = await _get_tenant_token(app_id=app_id)

    elements = []

    # 1. Natural Language Question Block
    elements.append({
        "tag": "markdown",
        "content": f"**🔍 提问问题：**\n{question}"
    })

    # 2. Collapsible Thinking Process Block (Temporarily commented out per user request for a cleaner, premium UI)
    # if result.thinking_process:
    #     thoughts_text = "\n".join([f"> {t}" for t in result.thinking_process if t.strip()])
    #     if thoughts_text:
    #         elements.append({
    #             "tag": "collapsible_panel",
    #             "expanded": False,
    #             "header": {
    #               "title": {"tag": "plain_text", "content": "⚙️ 显示思考过程 (Thinking Process)"}
    #             },
    #             "elements": [
    #               {
    #                 "tag": "markdown",
    #                 "content": thoughts_text
    #               }
    #             ]
    #         })

    # 3. Collapsible Generated SQL Block
    if result.sql:
        elements.append({
            "tag": "collapsible_panel",
            "expanded": False,
            "header": {
              "title": {"tag": "plain_text", "content": "💻 查看自动生成的 SQL 语句"}
            },
            "elements": [
              {
                "tag": "markdown",
                "content": f"```sql\n{result.sql}\n```"
              }
            ]
        })

    # 4. Dynamic Data Table Block
    if result.rows and result.fields:
        main_rows = result.rows[:10]
        remaining_rows = result.rows[10:]

        table_md = "| " + " | ".join(result.fields) + " |\n"
        table_md += "| " + " | ".join(["---"] * len(result.fields)) + " |\n"
        for row in main_rows:
            table_md += "| " + " | ".join(str(row.get(f, "-")) for f in result.fields) + " |\n"

        elements.append({
            "tag": "markdown",
            "content": f"**📊 数据查询结果展示：**\n\n{table_md}"
        })

        if remaining_rows:
            rem_table_md = "| " + " | ".join(result.fields) + " |\n"
            rem_table_md += "| " + " | ".join(["---"] * len(result.fields)) + " |\n"
            for row in remaining_rows[:30]:
                rem_table_md += "| " + " | ".join(str(row.get(f, "-")) for f in result.fields) + " |\n"
            
            rem_count = len(remaining_rows)
            if rem_count > 30:
                rem_table_md += f"\n*⚠️ 仅展示前 30 行余量数据。*"

            elements.append({
                "tag": "collapsible_panel",
                "expanded": False,
                "header": {
                  "title": {"tag": "plain_text", "content": f"🔽 展开查看其余 {rem_count} 行数据"}
                },
                "elements": [
                  {
                    "tag": "markdown",
                    "content": rem_table_md
                  }
                ]
            })

    # 4.5. Render Chart (Try Feishu Native VChart Component first, fallback to Option A PNG Image)
    vega_cfg = getattr(result, "vega_config", None)
    if vega_cfg:
        sql_str = getattr(result, "sql", None)
        vega_cfg = translate_spec_fields(vega_cfg, sql=sql_str)
        vchart_spec = vega_to_vchart(vega_cfg, sql=sql_str)
        if vchart_spec:
            logger.info("Successfully translated Vega spec to Feishu VChart spec!")
            elements.append({
                "tag": "markdown",
                "content": "**📈 可视化数据图表：**"
            })
            elements.append({
                "tag": "chart",
                "chart_spec": vchart_spec
            })
        else:
            try:
                import vl_convert as vlc
                opt_spec = _optimize_vega_spec(vega_cfg)
                png_bytes = vlc.vegalite_to_png(vl_spec=opt_spec, scale=2)
                image_key = await upload_image(png_bytes, app_id=app_id)
                if image_key:
                    elements.append({
                        "tag": "markdown",
                        "content": "**📈 可视化数据趋势图：**"
                    })
                    elements.append({
                        "tag": "img",
                        "img_key": image_key,
                        "alt": {
                            "tag": "plain_text",
                            "content": "数据可视化趋势图"
                        },
                        "mode": "fit_horizontal"
                    })
            except Exception as e:
                logger.exception("Failed to render/upload Vega chart image")

    # 5. Cleaned Summary (Business Insight / Analysis) - Moved to the end of informative content
    elements.append({
        "tag": "markdown",
        "content": cleaned_summary or "查询成功，请见下方明细。"
    })

    # 5.5. Chart and Interactive Page Action Buttons (Temporarily commented out per user's strict 5-section layout requirement)
    # if result_url:
    #     if result.vega_config:
    #         elements.append({
    #             "tag": "markdown",
    #             "content": "**📈 可视化图表已生成：**\n系统已为您绘制好专业的交互式数据分析图表（支持缩放/悬浮）。"
    #         })
    #         elements.append({
    #             "tag": "button",
    #             "text": {"tag": "plain_text", "content": "📊 点击查看交互式分析图表"},
    #             "url": result_url,
    #             "type": "primary"
    #         })
    #     else:
    #         elements.append({
    #             "tag": "button",
    #             "text": {"tag": "plain_text", "content": "🔍 查看完整网页版数据报表"},
    #             "url": result_url,
    #             "type": "default"
    #         })

    # 6. Interactive Actions / Recommended Questions Block (Temporarily Commented Out)
    # if result.recommended_questions:
    #     elements.append({
    #         "tag": "markdown",
    #         "content": "**🚀 快捷追问深度分析：**"
    #     })
    #     for q in result.recommended_questions[:3]:
    #         display_title = q[:18] + "..." if len(q) > 18 else q
    #         elements.append({
    #             "tag": "button",
    #             "text": {"tag": "plain_text", "content": f"💬 {display_title}"},
    #             "type": "default",
    #             "value": {
    #                 "action": "quick_query",
    #                 "query": q
    #             }
    #         })

    # Create Lark Card Schema 2.0 Content
    card_content = {
        "schema": "2.0",
        "config": {
            "wide_screen_mode": True,
            "enable_forward": True
        },
        "header": {
            "template": "indigo",
            "title": {
                "tag": "plain_text",
                "content": "📊 BQCA 智能数据分析"
            }
        },
        "body": {
            "direction": "vertical",
            "elements": elements
        }
    }

    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card_content),
    }

    receive_id_type = "open_id" if chat_id.startswith("ou_") else "chat_id"
    logger.info("Sending premium interactive card to %s (type=%s), elements_count=%d", chat_id, receive_id_type, len(elements))
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        resp_json = resp.json()
        if resp.status_code != 200 or resp_json.get("code") != 0:
            logger.error("Feishu API error! Status: %d, Response: %s", resp.status_code, json.dumps(resp_json, ensure_ascii=False))
        else:
            logger.info("Feishu premium interactive card sent successfully.")
        return resp_json

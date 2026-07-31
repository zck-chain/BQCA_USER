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


def vega_to_vchart(spec: dict) -> dict | None:
    """Convert Vega-Lite JSON spec into Feishu VChart spec dictionary.

    Returns VChart spec dict if converted successfully, or None for fallback.
    """
    if not isinstance(spec, dict):
        return None
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
            vchart = {
                "type": "bar",
                "title": title_cfg,
                "data": data_spec,
                "xField": x_field,
                "yField": y_field,
            }
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
        vchart_spec = vega_to_vchart(vega_cfg)
        if vchart_spec:
            logger.info("Successfully translated Vega spec to Feishu VChart spec!")
            elements.append({
                "tag": "markdown",
                "content": "**📈 可视化数据图表 (飞书原生 VChart 渲染)：**"
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

    # 6. Interactive Actions / Recommended Questions Block
    if result.recommended_questions:
        elements.append({
            "tag": "markdown",
            "content": "**🚀 快捷追问深度分析：**"
        })
        for q in result.recommended_questions[:3]:
            display_title = q[:18] + "..." if len(q) > 18 else q
            elements.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"💬 {display_title}"},
                "type": "default",
                "value": {
                    "action": "quick_query",
                    "query": q
                }
            })

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

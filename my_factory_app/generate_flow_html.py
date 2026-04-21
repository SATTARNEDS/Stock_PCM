"""
สร้างไฟล์ HTML จาก FLOW_DIAGRAM.md
- ใช้ mermaid.js render diagram ใน browser โดยตรง (ไม่พึ่ง API ภายนอก)
- ทั้งไฟล์เป็น A4 Landscape
- แต่ละ diagram คนละหน้า
"""
import re, os, webbrowser, html as html_lib

INPUT_FILE  = "FLOW_DIAGRAM.md"
OUTPUT_FILE = "FLOW_DIAGRAM_export.html"

DIAGRAM_MARKER = "<<<DIAGRAM>>>"


def convert(md_text: str) -> list:
    """แปลง markdown เป็น list ของ (type, content)
    type: 'html' | 'diagram'
    """
    # แยก mermaid blocks ออกก่อน
    parts = re.split(r"```mermaid\n(.*?)```", md_text, flags=re.DOTALL)
    # parts สลับกัน: text, diagram, text, diagram, ...

    result = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            # ส่วน markdown text
            html_chunk = md_to_html(part)
            if html_chunk.strip():
                result.append(("html", html_chunk))
        else:
            # ส่วน mermaid diagram
            result.append(("diagram", part.strip()))

    return result


def md_to_html(md_text: str) -> str:
    # แทน code blocks ปกติ
    md_text = re.sub(
        r"```[^\n]*\n(.*?)```",
        lambda m: f"<pre><code>{html_lib.escape(m.group(1))}</code></pre>",
        md_text, flags=re.DOTALL
    )

    lines = md_text.split("\n")
    out = []
    in_table = False
    table_rows = []
    header_done = False

    for line in lines:
        if line.startswith("######"):
            out.append(f"<h6>{line[6:].strip()}</h6>")
        elif line.startswith("#####"):
            out.append(f"<h5>{line[5:].strip()}</h5>")
        elif line.startswith("####"):
            out.append(f"<h4>{line[4:].strip()}</h4>")
        elif line.startswith("###"):
            out.append(f"<h3>{line[3:].strip()}</h3>")
        elif line.startswith("##"):
            out.append(f"<h2>{line[2:].strip()}</h2>")
        elif line.startswith("#"):
            out.append(f"<h1>{line[1:].strip()}</h1>")
        elif line.startswith("|"):
            if not in_table:
                in_table = True
                table_rows = []
                header_done = False
            table_rows.append(line)
        elif in_table and not line.startswith("|"):
            in_table = False
            out.append('<table><thead>')
            for tr in table_rows:
                cells = [c.strip() for c in tr.strip("|").split("|")]
                if all(re.match(r"^[-:]+$", c.strip()) for c in cells if c.strip()):
                    if not header_done:
                        out.append('</thead><tbody>')
                        header_done = True
                    continue
                tag = "th" if not header_done else "td"
                out.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>")
            out.append('</tbody></table>')
            table_rows = []
            if line.strip():
                out.append(f"<p>{line}</p>")
        elif line.strip() == "---":
            out.append("<hr>")
        elif line.startswith("> "):
            out.append(f"<blockquote>{line[2:]}</blockquote>")
        elif line.startswith("- ") or re.match(r"^\d+\. ", line):
            content = re.sub(r"^[-\d]+[.\s]+", "", line)
            content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", content)
            content = re.sub(r"`(.+?)`", r"<code>\1</code>", content)
            out.append(f"<li>{content}</li>")
        elif line.startswith("<pre>") or line.startswith("<code>"):
            out.append(line)
        elif line.strip() == "":
            pass  # skip blank lines
        else:
            line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
            line = re.sub(r"`(.+?)`", r"<code>\1</code>", line)
            out.append(f"<p>{line}</p>")

    # close open table
    if in_table and table_rows:
        out.append('<table><tbody>')
        for tr in table_rows:
            cells = [c.strip() for c in tr.strip("|").split("|")]
            if all(re.match(r"^[-:]+$", c.strip()) for c in cells if c.strip()):
                continue
            out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        out.append('</tbody></table>')

    return "\n".join(out)


def build_body(parts: list) -> str:
    body_parts = []
    diagram_count = 0
    for i, (kind, content) in enumerate(parts):
        if kind == "html":
            body_parts.append(f'<div class="text-section">{content}</div>')
        elif kind == "diagram":
            diagram_count += 1
            # Find preceding heading from previous html block to show as title
            title = ""
            if i > 0 and parts[i-1][0] == "html":
                prev_html = parts[i-1][1]
                headings = re.findall(r"<h[123]>(.*?)</h[123]>", prev_html)
                if headings:
                    title = f'<div class="diagram-title">{headings[-1]}</div>'

            escaped = html_lib.escape(content)
            body_parts.append(
                f'<div class="diagram-page">'
                f'{title}'
                f'<div class="mermaid">{content}</div>'
                f'</div>'
            )
    return "\n".join(body_parts)


def main():
    with open(INPUT_FILE, encoding="utf-8") as f:
        md_text = f.read()

    # Remove the <style> block we added earlier if present
    md_text = re.sub(r"<style>.*?</style>\n*", "", md_text, flags=re.DOTALL)

    parts = convert(md_text)
    body  = build_body(parts)

    html = f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<title>FLOW DIAGRAM - Stock PCM</title>
<link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@300;400;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>
  mermaid.initialize({{
    startOnLoad: true,
    theme: 'default',
    flowchart: {{ useMaxWidth: true, htmlLabels: true }},
    fontSize: 14
  }});
</script>
<style>
@page {{
  size: A4 landscape;
  margin: 12mm 15mm;
}}

* {{ box-sizing: border-box; }}

body {{
  font-family: 'Sarabun', sans-serif;
  font-size: 12pt;
  line-height: 1.8;
  color: #222;
  margin: 0;
  padding: 0;
}}

/* ===== Text section (จำกัดความกว้าง) ===== */
.text-section {{
  max-width: 220mm;
  margin: 0 auto;
  padding: 6mm 0;
}}

h1 {{ font-size: 18pt; border-bottom: 2px solid #333; padding-bottom: 6px; margin-top: 10px; }}
h2 {{ font-size: 14pt; border-bottom: 1px solid #aaa; color: #1a237e; margin-top: 16px; }}
h3 {{ font-size: 12pt; color: #283593; margin-top: 10px; }}
h4, h5, h6 {{ margin-top: 8px; }}

blockquote {{
  border-left: 4px solid #999;
  margin: 6px 0;
  padding: 4px 14px;
  background: #f5f5f5;
  color: #555;
}}

table {{
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0;
  font-size: 10pt;
}}
th {{ background: #e8eaf6; padding: 6px; text-align: left; border: 1px solid #ccc; }}
td {{ padding: 5px 7px; border: 1px solid #ccc; }}
tr:nth-child(even) {{ background: #fafafa; }}

code {{ background: #f0f0f0; padding: 1px 4px; border-radius: 3px; font-size: 10pt; }}
pre {{ background: #f4f4f4; padding: 10px; border-radius: 5px; font-size: 10pt; }}
hr {{ border: none; border-top: 1px solid #ddd; margin: 12px 0; }}
li {{ margin: 2px 0; }}
p {{ margin: 4px 0; }}

/* ===== Diagram page ===== */
.diagram-page {{
  page-break-before: always;
  page-break-inside: avoid;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 100%;
  min-height: 170mm;
  padding: 4mm 0;
}}

.diagram-title {{
  font-size: 13pt;
  font-weight: 600;
  color: #1a237e;
  border-bottom: 1px solid #aaa;
  width: 100%;
  text-align: center;
  margin-bottom: 8px;
  padding-bottom: 4px;
}}

/* Force mermaid SVG to fill landscape width */
.diagram-page .mermaid {{
  width: 100%;
  display: flex;
  justify-content: center;
}}
.diagram-page .mermaid svg {{
  max-width: 250mm !important;
  max-height: 160mm !important;
  width: 100% !important;
  height: auto !important;
}}

@media print {{
  body {{ padding: 0; }}
  .diagram-page {{ page-break-before: always; }}
}}
</style>
</head>
<body>
{body}
</body>
</html>"""

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ {OUTPUT_FILE} สร้างสำเร็จ")
    print("   รอให้ Chrome render diagram เสร็จก่อน (ประมาณ 3-5 วินาที) แล้วกด Ctrl+P → Save as PDF")
    webbrowser.open(os.path.abspath(OUTPUT_FILE))


if __name__ == "__main__":
    main()

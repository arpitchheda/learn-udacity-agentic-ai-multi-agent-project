"""
Renders workflow_diagram.mmd into workflow_diagram.html using an inline
Mermaid.js CDN — open the HTML in a browser and screenshot/export to PNG.
"""

mermaid_code = open("workflow_diagram.mmd").read()

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Munder Difflin — Agent Workflow Diagram</title>
  <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
  <style>
    body {{
      font-family: Arial, sans-serif;
      background: #f0f4f8;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 40px 20px;
    }}
    h1 {{ color: #1a1a2e; margin-bottom: 8px; font-size: 26px; }}
    p {{ color: #555; margin-bottom: 32px; font-size: 16px; }}
    .diagram-wrapper {{
      background: white;
      padding: 48px;
      border-radius: 16px;
      box-shadow: 0 4px 24px rgba(0,0,0,0.12);
      width: 100%;
      max-width: 1600px;
      overflow-x: auto;
    }}
    .mermaid {{
      font-size: 18px !important;
    }}
    /* Make rendered SVG large enough to read comfortably */
    .mermaid svg {{
      min-width: 1200px;
      height: auto;
    }}
  </style>
</head>
<body>
  <h1>Munder Difflin Paper Company — Multi-Agent System</h1>
  <p>Agent workflow diagram showing orchestration, tools, and data flows</p>
  <div class="diagram-wrapper">
    <div class="mermaid">
{mermaid_code}
    </div>
  </div>
  <script>
    mermaid.initialize({{
      startOnLoad: true,
      theme: 'default',
      flowchart: {{ curve: 'basis', padding: 20 }},
      themeVariables: {{
        fontSize: '18px',
        fontFamily: 'Arial, sans-serif',
        primaryColor: '#dbeafe',
        primaryTextColor: '#1e3a5f',
        primaryBorderColor: '#3b82f6',
        lineColor: '#6b7280',
        secondaryColor: '#fef9c3',
        tertiaryColor: '#dcfce7'
      }}
    }});
  </script>
</body>
</html>
"""

with open("workflow_diagram.html", "w") as f:
    f.write(html)

print("workflow_diagram.html generated — open in a browser to view/export.")

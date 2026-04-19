#!/usr/bin/env python3
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from datetime import datetime
import uuid
import json

BASE_DIR = Path(__file__).parent.parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
REPORTS_DIR = BASE_DIR / "reports"

REPORTS_DIR.mkdir(exist_ok=True)


def get_severity_class(severity):
    mapping = {
        "严重": "critical",
        "高危": "high",
        "中危": "medium",
        "低危": "low",
        "信息": "info",
        "critical": "critical",
        "high": "high",
        "medium": "medium",
        "low": "low",
        "info": "info",
        "informational": "info"
    }
    return mapping.get(severity, "info")


def get_risk_level_class(risk_level):
    mapping = {
        "极高": "text-red-700 font-bold",
        "高危": "text-orange-600 font-bold",
        "中危": "text-yellow-600 font-bold",
        "低危": "text-green-600 font-bold",
        "安全": "text-blue-600 font-bold"
    }
    return mapping.get(risk_level, "text-gray-600 font-bold")


class ReportGenerator:
    def __init__(self):
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        self.template = self.env.get_template("report_template.html")

    def generate(self, task_data, output_path=None):
        report_id = str(uuid.uuid4())
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        context = {
            "report_id": report_id,
            "report_title": task_data.get("target", "渗透测试") + " 测试报告",
            "generated_at": generated_at,
            "target": task_data.get("target", "未知目标"),
            "summary": task_data.get("summary", "渗透测试完成"),
            "findings": task_data.get("findings", []),
            "llm_analysis_raw": task_data.get("llm_analysis_raw", "")
        }

        html_content = self.template.render(**context)

        if not output_path:
            output_path = REPORTS_DIR / f"report_{report_id[:8]}.html"

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        return str(output_path), html_content

    def from_findings_file(self, findings_path, output_path=None):
        with open(findings_path, "r", encoding="utf-8") as f:
            findings_data = json.load(f)

        task_data = {
            "target": Path(findings_path).stem,
            "findings": findings_data.get("findings", []),
            "task_type": "漏洞扫描"
        }

        return self.generate(task_data, output_path)

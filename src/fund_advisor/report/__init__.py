"""报告模块"""

from fund_advisor.report.console import render_console_report
from fund_advisor.report.html_report import render_html_report

__all__ = ["render_console_report", "render_html_report"]

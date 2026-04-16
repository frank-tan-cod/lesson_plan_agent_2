"""Smoke test for lesson-plan export."""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.schemas import PlanCreate
from backend.app.services.export_service import ExportService, ExportUnavailableError
from backend.app.services.plan_service import PlanService


def main() -> None:
    """Run a basic export smoke test."""
    init_db()

    with session_maker() as session:
        plan_service = PlanService(session)
        export_service = ExportService(plan_service)

        plan = plan_service.create(
            PlanCreate(
                title="导出测试教案",
                subject="语文",
                grade="四年级",
                content={
                    "sections": [
                        {
                            "title": "教学目标",
                            "content": "1. 理解课文主旨\n2. 体会作者情感",
                        },
                        {
                            "title": "教学过程",
                            "content": "- 导入：播放图片\n- 新授：分组讨论\n![图片：荷叶图](upload_needed)",
                        },
                    ]
                },
            )
        )

        docx_bytes = export_service.export_to_docx(plan.id)

        with NamedTemporaryFile(suffix=".docx", delete=False) as temp_file:
            temp_file.write(docx_bytes)
            output_path = Path(temp_file.name)

        assert output_path.stat().st_size > 0
        assert docx_bytes[:2] == b"PK"

        print("DOCX export smoke test passed.")
        print(f"DOCX path: {output_path}")

        try:
            pdf_bytes = export_service.export_to_pdf(plan.id)
        except ExportUnavailableError as exc:
            print(f"PDF export skipped: {exc}")
        else:
            assert pdf_bytes.startswith(b"%PDF")
            print("PDF export smoke test passed.")


if __name__ == "__main__":
    main()

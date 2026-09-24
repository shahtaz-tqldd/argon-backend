import csv
import json
from io import BytesIO, StringIO

from django.utils import timezone
from openpyxl import Workbook


FIXED_EXPORT_COLUMNS = (
    "lead_id",
    "chatbot_id",
    "initial_ip_address",
    "last_ip_address",
    "detected_country_code",
    "detected_city",
    "status",
    "lead_score",
    "source",
    "created_at",
    "updated_at",
)


def _safe_spreadsheet_value(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    elif isinstance(value, (bool, int, float)):
        return value
    elif not isinstance(value, str):
        value = str(value)
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return value


def _timestamp(value):
    return timezone.localtime(value).isoformat() if value else ""


def _export_rows(leads):
    collected_keys = sorted(
        {
            key
            for lead in leads
            for key in lead.collected_fields
        }
    )
    collected_headers = {
        key: (
            f"collected_{key}"
            if key in FIXED_EXPORT_COLUMNS
            else key
        )
        for key in collected_keys
    }
    headers = (
        FIXED_EXPORT_COLUMNS[:2]
        + tuple(collected_headers.values())
        + FIXED_EXPORT_COLUMNS[2:]
    )
    rows = []
    for lead in leads:
        rows.append(
            (
                str(lead.id),
                str(lead.chatbot_id),
                *(lead.collected_fields.get(key) for key in collected_keys),
                lead.initial_ip_address,
                lead.last_ip_address,
                lead.detected_country_code,
                lead.detected_city,
                lead.status,
                lead.lead_score,
                lead.source,
                _timestamp(lead.created_at),
                _timestamp(lead.updated_at),
            )
        )
    return headers, rows


def _build_csv(headers, rows):
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(
        tuple(_safe_spreadsheet_value(value) for value in row)
        for row in rows
    )
    return output.getvalue().encode("utf-8-sig")


def _build_xlsx(headers, rows):
    output = BytesIO()
    workbook = Workbook(write_only=True)
    worksheet = workbook.create_sheet(title="Leads")
    worksheet.append(headers)
    for row in rows:
        worksheet.append(
            tuple(_safe_spreadsheet_value(value) for value in row)
        )
    workbook.save(output)
    return output.getvalue()


def build_lead_export(leads, file_format):
    headers, rows = _export_rows(leads)
    if file_format == "csv":
        return _build_csv(headers, rows)
    if file_format == "xlsx":
        return _build_xlsx(headers, rows)
    raise ValueError(f"Unsupported lead export format: {file_format}")

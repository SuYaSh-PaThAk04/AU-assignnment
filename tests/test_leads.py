from solution.catalog import load_schemas
from solution.ingest import header_lookup, project_row, validate_row


def _schema():
    return load_schemas()["motor_renewal"]


def test_alias_headers_map_onto_schema_fields():
    schema = _schema()
    lookup = header_lookup(schema)
    projected = project_row(
        lookup,
        {
            "Insured Name": "A Lead",
            "Mobile No": "9876543210",
            "Policy No": "MOT/1",
            "Expiry Date": "01-Dec-2026",
            "Premium": "₹12,500",
            "Language": "HI",
            "agent_notes": "VIP",
        },
    )
    assert projected["customer_name"] == "A Lead"
    assert projected["phone"] == "9876543210"
    assert projected["policy_number"] == "MOT/1"
    assert "agent_notes" not in projected


def test_first_schema_failure_wins_and_optional_invalid_is_a_warning():
    schema = _schema()
    missing_name = validate_row(
        schema,
        {"customer_name": "", "phone": "123", "policy_number": "MOT/1"},
    )
    assert missing_name.reason == "missing_required:customer_name"
    assert missing_name.warnings == 0

    bad_phone = validate_row(
        schema,
        {"customer_name": "C", "phone": "12345", "policy_number": "MOT/1"},
    )
    assert bad_phone.reason == "invalid_phone"

    missing_phone = validate_row(
        schema,
        {"customer_name": "F", "phone": "   ", "policy_number": "MOT/1"},
    )
    assert missing_phone.reason == "missing_required:phone"

    warnings = validate_row(
        schema,
        {
            "customer_name": "B",
            "phone": "9123456780",
            "policy_number": "MOT/2",
            "policy_expiry": "31/02/2026",
            "premium_amount": "N/A",
            "preferred_language": "tamil",
        },
    )
    assert warnings.reason is None
    assert warnings.warnings == 3
    assert warnings.values["phone"] == "+919123456780"
    assert warnings.values["policy_expiry"] is None
    assert warnings.values["premium_amount"] is None
    assert warnings.values["preferred_language"] is None

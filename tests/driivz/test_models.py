"""Tests for Driivz models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.mer.driivz.models import (
    ActiveTransaction,
    Bounds,
    SessionEstimate,
    Site,
    Socket,
    Station,
    Transaction,
    Wallet,
    clean_caption,
    ms_to_datetime,
    parse_start_time,
)
from tests.helpers import load_json_fixture


def test_clean_caption_strips_prefix_and_code() -> None:
    assert (
        clean_caption("[RESTRICTED ACCESS] (MER-FS-AD00457) Business Durham - NETPark 1 - Plexus")
        == "Business Durham - NETPark 1 - Plexus"
    )
    assert (
        clean_caption("Kings College London - Great Dover Street Apartments (MER-FS-AC00264)")
        == "Kings College London - Great Dover Street Apartments"
    )
    assert clean_caption("GB*B3V*EMERUKAD00057*1") == "GB*B3V*EMERUKAD00057*1"


def test_ms_to_datetime() -> None:
    assert ms_to_datetime(1789554866000) == datetime(2026, 9, 16, 10, 34, 26, tzinfo=UTC)
    assert ms_to_datetime(None) is None
    assert ms_to_datetime("bad") is None


def test_active_transaction_from_dict_derives_start_from_elapsed() -> None:
    now = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)
    tx = ActiveTransaction.from_dict(load_json_fixture("transaction_start_time.json")["data"])
    assert tx.transaction_id == 9088676
    assert tx.boost_enabled is False
    assert tx.elapsed == timedelta(milliseconds=953622)
    assert tx.started_at(now) == now - timedelta(milliseconds=953622)


def test_active_transaction_tolerates_missing_duration() -> None:
    now = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)
    tx = ActiveTransaction.from_dict({"transactionId": 1})
    assert tx.elapsed is None
    assert tx.started_at(now) is None


def test_parse_start_time_prefers_absolute_then_elapsed() -> None:
    now = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)
    # a bare epoch (not wrapped in a dict) is still accepted directly
    assert parse_start_time(1789554866000, now) == datetime(2026, 9, 16, 10, 34, 26, tzinfo=UTC)
    # an absolute timestamp still wins, for portals that send one
    assert parse_start_time({"startOn": 1789554866000}, now) == datetime(
        2026, 9, 16, 10, 34, 26, tzinfo=UTC
    )
    # the real Mer payload carries only elapsed milliseconds
    assert parse_start_time(
        load_json_fixture("transaction_start_time.json")["data"], now
    ) == now - timedelta(milliseconds=953622)
    assert parse_start_time(None, now) is None
    assert parse_start_time({}, now) is None


def test_bounds() -> None:
    b = Bounds.around(54.67043, -1.45045)
    assert b.to_dict() == {
        "northEastLat": 54.67343,
        "northEastLng": -1.44745,
        "southWestLat": 54.66743,
        "southWestLng": -1.45345,
    }
    assert Bounds.UK.to_dict()["northEastLat"] == 61.0


def test_site_from_dict() -> None:
    raw = load_json_fixture("sites_in_bounds.json")["data"][0]
    site = Site.from_dict(raw)
    assert site.id == 2877
    assert site.name == "Durham County Council - Business Durham NETPark"
    assert site.status == "AVAILABLE"
    assert site.socket_count == 8
    assert site.access_level == "PUBLIC"
    assert site.latitude == 54.67043
    assert site.charging_speed == "SEMI_FAST"


def test_station_detail_from_dict() -> None:
    raw = load_json_fixture("station_6042.json")["data"]
    station = Station.from_dict(raw)
    assert station.id == 6042
    assert station.display_name == "Business Durham - NETPark 3 - Explorer 1"
    assert station.is_restricted is False
    assert station.site_id == 2877
    assert station.identity_key == "MER-FS-AD00137"
    assert station.model_name == "Eve Double Pro-line"
    assert station.owner_name == "Durham County Council"
    assert station.status == "AVAILABLE"
    assert [s.name for s in station.sockets] == ["Left", "Right"]
    left = station.sockets[0]
    assert left.id == 11243
    assert left.station_id == 6042
    assert left.identity_key == "1"
    assert left.max_power_kw == 7
    assert left.socket_type == "TYPE_2_MENNEKES"
    assert left.voltage_type == "AC"
    assert left.price_per_kwh == 0
    assert left.prices[0].billing_plan_id == 3465
    assert left.prices[0].currency == "GBP"
    assert left.is_available is True
    assert left.is_in_use is False


def test_station_short_from_dict_defaults() -> None:
    raw = load_json_fixture("stations_in_bounds.json")["data"][0]
    station = Station.from_dict(raw)
    assert station.site_id is None
    assert station.sockets[0].status == "UNKNOWN"
    assert station.sockets[0].name is None
    assert station.sockets[0].price_per_kwh is None


def test_station_with_live_merges_status() -> None:
    detail = Station.from_dict(load_json_fixture("station_6042.json")["data"])
    live_raw = dict(load_json_fixture("stations_by_ids.json")["data"][0])
    live_raw["stationStatusId"] = "CHARGING"
    live_raw["stationSockets"][1]["socketStatusId"] = "CHARGING"
    merged = detail.with_live(Station.from_dict(live_raw))
    assert merged.status == "CHARGING"
    assert merged.sockets[0].name == "Left"
    assert merged.sockets[0].status == "AVAILABLE"
    assert merged.sockets[1].name == "Right"
    assert merged.sockets[1].status == "CHARGING"
    assert merged.sockets[1].is_in_use is True
    assert merged.model_name == "Eve Double Pro-line"


def test_socket_restricted_flag() -> None:
    station = Station.from_dict(
        {"id": 1, "caption": "[RESTRICTED ACCESS] (MER-FS-X) Foo", "stationStatusId": "AVAILABLE"}
    )
    assert station.is_restricted is True
    assert station.display_name == "Foo"
    assert station.sockets == ()


def test_wallet_from_dict() -> None:
    wallet = Wallet.from_dict(load_json_fixture("wallet.json")["data"])
    assert wallet.id == 228000
    assert wallet.customer_id == 123456
    assert wallet.member_id == 123123
    assert wallet.account_number == 1123456
    assert wallet.balance == 12.5
    assert wallet.currency == "GBP"
    assert wallet.timezone == "Europe/London"


def test_transaction_from_dict() -> None:
    tx = Transaction.from_dict(load_json_fixture("transactions.json")["data"][0])
    assert tx.id == 9084600
    assert tx.station_id == 6041
    assert tx.display_name == "Business Durham - NETPark 4 - Explorer 2"
    assert tx.started_at == datetime(2026, 9, 16, 10, 34, 26, tzinfo=UTC)
    assert tx.stopped_at == datetime(2026, 9, 16, 15, 27, 47, tzinfo=UTC)
    assert tx.duration_s == 17601
    assert tx.energy_kwh == 32.408
    assert tx.cost == 0
    assert tx.currency == "GBP"
    assert tx.billing_plan_name == "Durham County Council - Netpark IP"


def test_session_estimate_reads_the_real_payload() -> None:
    est = SessionEstimate.from_dict(load_json_fixture("transaction_estimate.json")["data"])
    assert est.energy_kwh == 1.606
    assert est.cost == 0
    assert est.currency == "GBP"
    assert est.duration == timedelta(milliseconds=953825)
    assert est.rate_estimation == 1.801


def test_session_estimate_still_reads_alternative_spellings() -> None:
    est = SessionEstimate.from_dict({"energyKwh": 3.2, "totalCost": "0.80"})
    assert est.energy_kwh == 3.2
    assert est.cost == 0.8
    assert est.duration is None
    assert est.rate_estimation is None
    assert est.currency is None
    # totalEnergy and energyConsumed are watt-hours, converted with the 1000.0 divisor
    est2 = SessionEstimate.from_dict({"totalEnergy": 12345, "cost": 1.5, "currency": "GBP"})
    assert est2.energy_kwh == 12.345
    assert est2.cost == 1.5
    assert est2.currency == "GBP"


def test_socket_from_dict_minimal() -> None:
    socket = Socket.from_dict({"id": 5})
    assert socket.status == "UNKNOWN"
    assert socket.prices == ()

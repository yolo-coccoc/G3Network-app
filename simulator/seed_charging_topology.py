"""Provision topology charging tối thiểu cho simulator OCPP local.

Script gọi lần lượt API tạo station, EVSE và connector. Nó không tự tạo lại
topology đã tồn tại, không retry và không chứa logic nghiệp vụ charging; mỗi
lần chạy nên dùng một ``ocpp_identity`` mới nếu database đã có identity đó.
"""

import argparse
import json
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_IDENTITY = "STEP7-OCPP-001"
DEFAULT_DISPLAY_NAME = "Charging Simulator"
REQUEST_TIMEOUT_SECONDS = 10


def post_json(
    api_url: str,
    path: str,
    payload: Mapping[str, object],
) -> dict[str, Any]:
    """Gửi một POST JSON tới backend và trả về response object.

    Args:
        api_url: Base URL của backend, không bắt buộc có dấu ``/`` cuối.
        path: API path tương đối.
        payload: JSON object gửi trong request body.

    Returns:
        JSON response dạng object.

    Raises:
        RuntimeError: Nếu request lỗi, response không phải JSON object hoặc
            backend trả về HTTP status lỗi.
    """
    request = Request(
        f"{api_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw_response = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8")
        raise RuntimeError(f"POST {path} trả về HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Không thể gọi POST {path}: {error.reason}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"POST {path} trả về JSON không hợp lệ") from error

    if not isinstance(raw_response, dict):
        raise RuntimeError(f"POST {path} không trả về JSON object")
    return raw_response


def required_id(response: Mapping[str, Any], field_name: str) -> str:
    """Lấy internal ID từ response provision và kiểm tra kiểu dữ liệu.

    Args:
        response: JSON object trả về bởi API.
        field_name: Tên field ID cần lấy.

    Returns:
        ID dạng chuỗi để đưa vào URL của request kế tiếp.

    Raises:
        RuntimeError: Nếu response thiếu ID hoặc ID không phải chuỗi.
    """
    value = response.get(field_name)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Response thiếu {field_name} hợp lệ: {response}")
    return value


def positive_int(value: str) -> int:
    """Parse một số nguyên dương cho tham số topology OCPP.

    Args:
        value: Chuỗi số nhận từ CLI.

    Returns:
        Số nguyên dương.

    Raises:
        argparse.ArgumentTypeError: Nếu value không phải số nguyên dương.
    """
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("phải là số nguyên dương") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("phải là số nguyên dương")
    return parsed


def parse_args() -> argparse.Namespace:
    """Đọc tham số CLI cho một topology simulator.

    Returns:
        Namespace chứa URL backend, identity và OCPP IDs.
    """
    parser = argparse.ArgumentParser(
        description="Tạo station, EVSE và connector cho OCPP simulator."
    )
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--identity", default=DEFAULT_IDENTITY)
    parser.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME)
    parser.add_argument("--evse-id", type=positive_int, default=1)
    parser.add_argument("--connector-id", type=positive_int, default=1)
    return parser.parse_args()


def main() -> None:
    """Tạo station, EVSE và connector theo đúng thứ tự foreign key."""
    args = parse_args()
    station = post_json(
        args.api_url,
        "/api/v1/charging-stations",
        {
            "ocpp_identity": args.identity,
            "display_name": args.display_name,
        },
    )
    station_id = required_id(station, "station_id")

    evse = post_json(
        args.api_url,
        f"/api/v1/charging-stations/{station_id}/evses",
        {"ocpp_evse_id": args.evse_id},
    )
    evse_id = required_id(evse, "evse_id")

    connector = post_json(
        args.api_url,
        f"/api/v1/charging-evses/{evse_id}/connectors",
        {"ocpp_connector_id": args.connector_id},
    )
    connector_id = required_id(connector, "connector_id")

    print(f"Đã provision station: {args.identity} ({station_id})")
    print(f"Đã provision EVSE: {args.evse_id} ({evse_id})")
    print(f"Đã provision connector: {args.connector_id} ({connector_id})")


if __name__ == "__main__":
    main()

"""The device health classification of the dashboard (DEV-04).

Pure, no I/O. One rule, so the device list, the single-device view and the
network summary can never disagree on what "healthy" means. The inputs are
facts the caller has already read (status, whether the device is mounted on a
live truck, the silence flag of ``monitoring/silence_rule`` and the newest
status report's SIM and GNSS fields).
"""

from datetime import datetime

from app.domains.telematics.types import TelematicHealthState, TelematicStatus

# Mobile data status that means "working". Provisional (mqtt-spec.md 2.2):
# any other value the vendor sends is treated as a problem to look at.
SIM_DATA_STATUS_OK = "ACTIVE"
# Satellite status that is a fault of the device; NO_FIX alone is normal
# indoors or under cover, so it is not a problem.
GNSS_STATUS_FAULT = "ANTENNA_FAULT"


def classify_device_health(
    *,
    status: TelematicStatus,
    is_mounted: bool,
    last_seen_at: datetime | None,
    is_silent: bool,
    sim_data_status: str | None,
    gnss_status: str | None,
) -> TelematicHealthState:
    """Decide the health state of one device.

    Rule:
        ``INACTIVE`` before everything (a person took it out of service),
        then ``NOT_MOUNTED``, ``NO_DATA`` (never reported), ``SILENT``,
        ``ATTENTION`` (the newest status report shows mobile data other than
        ``ACTIVE`` or a GNSS antenna fault; a missing report is not a
        problem), else ``HEALTHY``.

    Args:
        status: Status set by a person.
        is_mounted: The device is mounted on a live (not deleted) truck.
        last_seen_at: Newest telemetry time of that truck, or `None` if it
            never reported .
        is_silent: The silence rule holds for the device.
        sim_data_status: Mobile data status of the newest status report, if any.
        gnss_status: Satellite status of the newest status report, if any.

    Returns:
        The device's health state.
    """
    if status is TelematicStatus.INACTIVE:
        return TelematicHealthState.INACTIVE
    if not is_mounted:
        return TelematicHealthState.NOT_MOUNTED
    if last_seen_at is None:
        return TelematicHealthState.NO_DATA
    if is_silent:
        return TelematicHealthState.SILENT
    if (
        sim_data_status is not None and sim_data_status != SIM_DATA_STATUS_OK
    ) or gnss_status == GNSS_STATUS_FAULT:
        return TelematicHealthState.ATTENTION
    return TelematicHealthState.HEALTHY

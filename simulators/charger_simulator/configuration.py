"""The charger's OCPP 1.6 configuration keys and how they are changed.

Answers ``GetConfiguration`` and ``ChangeConfiguration`` (OCPP 1.6 sections
5.8 and 5.3, keys from section 9.1 Core Profile). Each key has a type, so a
change is validated like a real charger does:

* an unknown key -> ``NotSupported``;
* a read-only key, or a value of the wrong shape -> ``Rejected``;
* otherwise the value is stored -> ``Accepted``.

Key names are matched without regard to case; values are stored as sent
(booleans as ``true`` / ``false``). The configuration lives in memory: every
power-on starts from the defaults below, except ``HeartbeatInterval``, which
the accepted BootNotification sets. ``HeartbeatInterval`` takes effect from
the next heartbeat; the other keys are stored and reported only, since the
simulator does not charge yet.
"""

from __future__ import annotations

from dataclasses import dataclass

# Measurands a sampled-data key may list (OCPP 1.6 section 7.31).
MEASURANDS = frozenset(
    {
        "Current.Export",
        "Current.Import",
        "Current.Offered",
        "Energy.Active.Export.Register",
        "Energy.Active.Import.Register",
        "Energy.Reactive.Export.Register",
        "Energy.Reactive.Import.Register",
        "Energy.Active.Export.Interval",
        "Energy.Active.Import.Interval",
        "Energy.Reactive.Export.Interval",
        "Energy.Reactive.Import.Interval",
        "Frequency",
        "Power.Active.Export",
        "Power.Active.Import",
        "Power.Factor",
        "Power.Offered",
        "Power.Reactive.Export",
        "Power.Reactive.Import",
        "RPM",
        "SoC",
        "Temperature",
        "Voltage",
    }
)


@dataclass(frozen=True)
class KeyDefinition:
    """One configuration key the charger supports.

    Attributes:
        key: The OCPP key name.
        kind: ``int`` (whole number >= ``minimum``), ``bool``, ``measurands``
            (comma-separated measurands) or ``text``.
        default: The value at power-on.
        readonly: Whether ``ChangeConfiguration`` must reject it.
        minimum: The smallest accepted number, for ``int`` keys.
    """

    key: str
    kind: str
    default: str
    readonly: bool = False
    minimum: int = 0


# The keys this charger reports, in the order it reports them.
KEY_DEFINITIONS: tuple[KeyDefinition, ...] = (
    KeyDefinition("AllowOfflineTxForUnknownId", "bool", "false"),
    KeyDefinition("AuthorizeRemoteTxRequests", "bool", "true"),
    KeyDefinition("ClockAlignedDataInterval", "int", "0"),
    KeyDefinition("ConnectionTimeOut", "int", "60", minimum=1),
    KeyDefinition("GetConfigurationMaxKeys", "int", "50", readonly=True),
    KeyDefinition("HeartbeatInterval", "int", "60", minimum=1),
    KeyDefinition("LocalAuthorizeOffline", "bool", "false"),
    KeyDefinition("LocalPreAuthorize", "bool", "false"),
    KeyDefinition(
        "MeterValuesAlignedData", "measurands", "Energy.Active.Import.Register"
    ),
    KeyDefinition(
        "MeterValuesSampledData",
        "measurands",
        "Energy.Active.Import.Register,Power.Active.Import,SoC",
    ),
    KeyDefinition("MeterValueSampleInterval", "int", "60"),
    KeyDefinition("NumberOfConnectors", "int", "2", readonly=True),
    KeyDefinition("ResetRetries", "int", "3"),
    KeyDefinition("StopTransactionOnEVSideDisconnect", "bool", "true"),
    KeyDefinition("StopTransactionOnInvalidId", "bool", "true"),
    KeyDefinition("StopTxnAlignedData", "measurands", "Energy.Active.Import.Register"),
    KeyDefinition("StopTxnSampledData", "measurands", "Energy.Active.Import.Register"),
    KeyDefinition("SupportedFeatureProfiles", "text", "Core", readonly=True),
    KeyDefinition("TransactionMessageAttempts", "int", "3"),
    KeyDefinition("TransactionMessageRetryInterval", "int", "60"),
    KeyDefinition("UnlockConnectorOnEVSideDisconnect", "bool", "true"),
    KeyDefinition("WebSocketPingInterval", "int", "0"),
)


def normalize_value(definition: KeyDefinition, value: str) -> str | None:
    """Check a new value against its key's type and return it as stored.

    Args:
        definition: The key being changed.
        value: The value the server sent.

    Returns:
        The value to store, or ``None`` when it has the wrong shape.
    """
    text = value.strip()
    if definition.kind == "int":
        if not text.isdigit() or int(text) < definition.minimum:
            return None
        return str(int(text))
    if definition.kind == "bool":
        lowered = text.lower()
        return lowered if lowered in ("true", "false") else None
    if definition.kind == "measurands":
        items = [item.strip() for item in text.split(",")]
        if not text or any(item not in MEASURANDS for item in items):
            return None
        return ",".join(items)
    return text


class ChargerConfiguration:
    """The charger's current configuration values.

    Used only from the asyncio loop (the OCPP handlers and the heartbeat
    loop), so it needs no lock.
    """

    def __init__(self) -> None:
        """Start from the power-on defaults."""
        self._definitions = {item.key.lower(): item for item in KEY_DEFINITIONS}
        self._values = {item.key: item.default for item in KEY_DEFINITIONS}

    @property
    def heartbeat_interval(self) -> int:
        """Seconds between heartbeats (``HeartbeatInterval``)."""
        return int(self._values["HeartbeatInterval"])

    def set_heartbeat_interval(self, seconds: int) -> None:
        """Store the interval given at boot (or by the local setting).

        Args:
            seconds: Seconds between heartbeats; at least 1.
        """
        self._values["HeartbeatInterval"] = str(max(1, seconds))

    def report(
        self, keys: list[str] | None
    ) -> tuple[list[dict[str, object]], list[str]]:
        """Build a ``GetConfiguration`` answer.

        Args:
            keys: The keys asked for; ``None`` or empty = all of them.

        Returns:
            ``(configurationKey entries, unknownKey names)``: each entry has
            ``key``, ``readonly`` and ``value``.
        """
        if not keys:
            wanted = [item.key for item in KEY_DEFINITIONS]
            unknown: list[str] = []
        else:
            wanted, unknown = [], []
            for name in keys:
                definition = self._definitions.get(name.lower())
                if definition is None:
                    unknown.append(name)
                else:
                    wanted.append(definition.key)
        entries: list[dict[str, object]] = []
        for key in wanted:
            definition = self._definitions[key.lower()]
            entries.append(
                {
                    "key": key,
                    "readonly": definition.readonly,
                    "value": self._values[key],
                }
            )
        return entries, unknown

    def change(self, key: str, value: str) -> str:
        """Apply a ``ChangeConfiguration`` request.

        Args:
            key: The key the server wants to change.
            value: Its new value.

        Returns:
            The OCPP status: ``Accepted``, ``Rejected`` (read-only key or bad
            value) or ``NotSupported`` (unknown key).
        """
        definition = self._definitions.get(key.lower())
        if definition is None:
            return "NotSupported"
        if definition.readonly:
            return "Rejected"
        normalized = normalize_value(definition, value)
        if normalized is None:
            return "Rejected"
        self._values[definition.key] = normalized
        return "Accepted"

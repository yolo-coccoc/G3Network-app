"""The sample car plugged into a gun: a simple battery and BMS model.

On a DC charger the car's battery-management system (BMS) decides the charge:
the charger only delivers what the car requests, up to what it can offer. So
the car, not the charger, gives the state of charge (SoC), the battery
voltage and the power curve:

* **Constant current** below ``TAPER_START_SOC``: the car asks for its full
  power.
* **Constant voltage** above it: the request falls linearly to
  ``FINAL_POWER_SHARE`` of full power at 100 %.
* **Full** at 100 %: the request is zero (the charger reports SuspendedEV).

One sample car only (an ordinary passenger EV); the numbers are typical, not
those of a particular model.
"""

from __future__ import annotations

from dataclasses import dataclass

# The sample car: battery capacity and the most DC power it accepts.
SAMPLE_CAR_NAME = "Sample car"
SAMPLE_BATTERY_KWH = 75.0
SAMPLE_MAX_POWER_KW = 150.0

# Battery voltage at 0 % and 100 % SoC (a 400 V-class pack).
VOLTAGE_EMPTY_V = 340.0
VOLTAGE_FULL_V = 410.0

# Where the power starts to fall, and how much is left just before full.
TAPER_START_SOC = 80.0
FINAL_POWER_SHARE = 0.05

# Battery temperature: ambient, plus a rise with the charging power.
AMBIENT_CELSIUS = 25.0
CELSIUS_PER_KW = 0.08


@dataclass
class Car:
    """One car on one gun, from plug-in to unplug.

    Attributes:
        name: What the page calls it.
        battery_kwh: Usable battery capacity.
        max_power_kw: The most DC power the car accepts.
        soc: State of charge in percent (0-100), kept as a float so a slow
            charge still moves it.
    """

    name: str
    battery_kwh: float
    max_power_kw: float
    soc: float

    @classmethod
    def sample(cls, start_soc: float) -> Car:
        """Build the sample car at a given state of charge.

        Args:
            start_soc: State of charge at plug-in, in percent.

        Returns:
            The car.
        """
        return cls(
            name=SAMPLE_CAR_NAME,
            battery_kwh=SAMPLE_BATTERY_KWH,
            max_power_kw=SAMPLE_MAX_POWER_KW,
            soc=max(0.0, min(100.0, start_soc)),
        )

    @property
    def is_full(self) -> bool:
        """Whether the battery is full (the BMS requests nothing more)."""
        return self.soc >= 100.0

    @property
    def stage(self) -> str:
        """The charging stage the charger's screen shows."""
        if self.is_full:
            return "Full"
        return "Constant current" if self.soc < TAPER_START_SOC else "Constant voltage"

    @property
    def voltage_v(self) -> float:
        """Battery voltage, rising with the state of charge."""
        return VOLTAGE_EMPTY_V + (VOLTAGE_FULL_V - VOLTAGE_EMPTY_V) * self.soc / 100

    def requested_power_kw(self) -> float:
        """The power the BMS asks for at the current state of charge.

        Returns:
            Kilowatts; zero when full.
        """
        if self.is_full:
            return 0.0
        if self.soc < TAPER_START_SOC:
            return self.max_power_kw
        share_left = (100.0 - self.soc) / (100.0 - TAPER_START_SOC)
        floor = FINAL_POWER_SHARE * self.max_power_kw
        return floor + (self.max_power_kw - floor) * share_left

    def temperature_celsius(self, power_kw: float) -> float:
        """Battery temperature while taking a given power.

        Args:
            power_kw: The power being delivered.

        Returns:
            Degrees Celsius.
        """
        return AMBIENT_CELSIUS + CELSIUS_PER_KW * power_kw

    def take_energy(self, energy_kwh: float) -> None:
        """Store delivered energy in the battery (raises the SoC, up to 100 %).

        Args:
            energy_kwh: Energy delivered since the last step.
        """
        self.soc = min(100.0, self.soc + 100.0 * energy_kwh / self.battery_kwh)

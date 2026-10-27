"""Static fictional world for Orbis Networks: platforms, suppliers, parts, BOM, alternates.

Everything here is invented. Nothing is random; the generator adds randomness on top.
"""

from dataclasses import dataclass
from datetime import date

AS_OF = date(2026, 11, 2)  # Monday; week_idx 0
HISTORY_WEEKS = 104
WEEKLY_BUCKETS = 26
GENERATOR_VERSION = "1.0.0"


@dataclass(frozen=True)
class Platform:
    platform_id: str
    description: str
    lifecycle_phase: str
    launch_date: date
    last_ship_date: date
    eosl_date: date
    asp_usd: float
    noise_sigma: float  # lognormal sigma for weekly shipment noise
    forecast_bias: float  # multiplier applied to the forecast (1.06 = over-forecast by 6%)
    service_per_week: float  # spares shipped per week (SERVICE channel)


PLATFORMS: list[Platform] = [
    Platform("ORB-S16", "Spine, 64x1.6T, 102.4T switch ASIC", "NPI", date(2026, 9, 1),
             date(2031, 12, 31), date(2036, 12, 31), 78000, 0.20, 1.00, 1.0),
    Platform("ORB-S8", "Spine, 64x800G, 51.2T ASIC", "SUSTAIN", date(2024, 3, 1),
             date(2029, 6, 30), date(2034, 6, 30), 52000, 0.14, 1.06, 2.0),
    Platform("ORB-S4", "Spine, 64x400G, 25.6T ASIC", "EOL_ANNOUNCED", date(2021, 2, 1),
             date(2027, 6, 30), date(2032, 6, 30), 31000, 0.16, 1.00, 3.0),
    Platform("ORB-L4", "Leaf, 48x100G + 8x400G, 12.8T ASIC", "SUSTAIN", date(2023, 5, 1),
             date(2029, 12, 31), date(2034, 12, 31), 24000, 0.18, 1.00, 3.0),
    Platform("ORB-L1", "Leaf, 48x25G + 8x100G, 3.2T ASIC", "EOL", date(2019, 4, 1),
             date(2026, 6, 30), date(2031, 6, 30), 11000, 0.0, 1.00, 3.0),
    Platform("ORB-M1", "Management switch, 48x1G + 4x10G", "SUSTAIN", date(2022, 1, 1),
             date(2030, 6, 30), date(2035, 6, 30), 4500, 0.12, 1.00, 2.0),
]  # fmt: skip

# (supplier_id, name, commodity, region, otd_rate, risk_tier)
SUPPLIERS: list[tuple[str, str, str, str, float, str]] = [
    ("SUP-01", "Silicore Semiconductor", "Switch ASICs", "APAC", 0.88, "HIGH"),
    ("SUP-02", "Arvane Micro", "CPU modules", "NA", 0.93, "MEDIUM"),
    ("SUP-03", "Kestrel Logic", "BMC, CPLD", "APAC", 0.90, "MEDIUM"),
    ("SUP-04", "Tervan Power", "Power supplies", "APAC", 0.91, "MEDIUM"),
    ("SUP-05", "Kaldor Thermal", "Fans", "APAC", 0.95, "LOW"),
    ("SUP-06", "Meridian Memory", "DDR modules", "APAC", 0.89, "HIGH"),
    ("SUP-07", "Halvex Interconnect", "Cages, connectors", "APAC", 0.94, "LOW"),
    ("SUP-08", "Lumora Timing", "Clock/jitter ICs", "EMEA", 0.92, "MEDIUM"),
    ("SUP-09", "Brightpath Photonics", "Retimers, PHYs", "NA", 0.87, "HIGH"),
    ("SUP-10", "Pacifica Contract Mfg", "PCBA, final assembly", "APAC", 0.96, "LOW"),
    ("SUP-11", "Norrland Metals", "Chassis, sheet metal", "EMEA", 0.97, "LOW"),
    ("SUP-12", "Quillon Storage", "Boot SSD/eMMC", "APAC", 0.93, "MEDIUM"),
]  # fmt: skip


@dataclass(frozen=True)
class PartSpec:
    part_id: str
    description: str
    commodity: str
    supplier_id: str
    unit_cost_usd: float
    lead_time_weeks: int
    moq: int
    order_multiple: int
    lifecycle_status: str = "ACTIVE"
    anchor: bool = False


def _p(
    part_id: str,
    description: str,
    commodity: str,
    supplier_id: str,
    cost: float,
    lead_time_weeks: int,
    moq: int,
    multiple: int,
    anchor: bool = False,
    status: str = "ACTIVE",
) -> PartSpec:
    return PartSpec(
        part_id, description, commodity, supplier_id, cost, lead_time_weeks, moq, multiple,
        status, anchor,
    )  # fmt: skip


PARTS: list[PartSpec] = [
    # ---- anchor parts (spec 01 section 1.3). Do not change attributes. ----
    _p("P-ASIC-102T", "102.4T switch ASIC", "ASIC", "SUP-01", 9500, 40, 10, 10, anchor=True),
    _p("P-ASIC-51T", "51.2T switch ASIC", "ASIC", "SUP-01", 5200, 36, 10, 10, anchor=True),
    _p("P-CPU-8C", "8-core x86 CPU module", "CPU", "SUP-02", 410, 18, 25, 25, anchor=True),
    _p("P-CPU-4C", "4-core x86 CPU module", "CPU", "SUP-02", 165, 16, 50, 50, anchor=True),
    _p("P-BMC-A", "Baseboard mgmt controller", "BMC", "SUP-03", 22, 20, 100, 100, anchor=True),
    _p("P-BMC-B", "Next-gen BMC (replacement for P-BMC-A)", "BMC", "SUP-03", 24, 20, 100, 100,
       anchor=True),
    _p("P-CPLD-S4", "Board CPLD unique to S4", "CPLD", "SUP-03", 38, 16, 50, 50, anchor=True),
    _p("P-PSU-3000AC", "3000W AC PSU", "PSU", "SUP-04", 260, 14, 20, 20, anchor=True),
    _p("P-PSU-2000AC", "2000W AC PSU", "PSU", "SUP-04", 180, 14, 20, 20, anchor=True),
    _p("P-PSU-650AC", "650W AC PSU", "PSU", "SUP-04", 70, 12, 50, 50, anchor=True),
    _p("P-FAN-80", "80mm fan module", "FAN", "SUP-05", 31, 9, 50, 50, anchor=True),
    _p("P-FAN-40", "40mm fan module", "FAN", "SUP-05", 14, 8, 100, 100, anchor=True),
    _p("P-DDR5-16G", "DDR5 16GB module", "MEMORY", "SUP-06", 95, 16, 50, 50, anchor=True),
    _p("P-DDR4-8G", "DDR4 8GB module", "MEMORY", "SUP-06", 41, 18, 50, 50, anchor=True,
       status="NRND"),
    _p("P-RTM-224G", "224G retimer", "RETIMER", "SUP-09", 120, 24, 100, 100, anchor=True),
    _p("P-CLK-J1", "Jitter-cleaner clock IC", "CLOCK", "SUP-08", 12, 16, 250, 250, anchor=True),
    _p("P-SSD-240", "240GB boot SSD", "SSD", "SUP-12", 28, 10, 100, 100, anchor=True),
    _p("P-PCBA-S16", "Main board assembly, ORB-S16", "PCBA", "SUP-10", 2400, 8, 10, 10,
       anchor=True),
    _p("P-PCBA-S8", "Main board assembly, ORB-S8", "PCBA", "SUP-10", 1800, 8, 10, 10, anchor=True),
    _p("P-PCBA-S4", "Main board assembly, ORB-S4", "PCBA", "SUP-10", 1100, 7, 10, 10, anchor=True),
    _p("P-PCBA-L4", "Main board assembly, ORB-L4", "PCBA", "SUP-10", 800, 7, 10, 10, anchor=True),
    _p("P-PCBA-L1", "Main board assembly, ORB-L1", "PCBA", "SUP-10", 300, 6, 10, 10, anchor=True),
    _p("P-PCBA-M1", "Main board assembly, ORB-M1", "PCBA", "SUP-10", 350, 6, 10, 10, anchor=True),
    # ---- generic parts that fill out each BOM ----
    _p("P-ASIC-25T", "25.6T switch ASIC", "ASIC", "SUP-01", 2900, 32, 10, 10),
    _p("P-ASIC-12T", "12.8T switch ASIC", "ASIC", "SUP-01", 1500, 30, 10, 10),
    _p("P-ASIC-3T", "3.2T switch ASIC", "ASIC", "SUP-01", 420, 26, 25, 25),
    _p("P-ASIC-MGT", "Management switch ASIC", "ASIC", "SUP-01", 85, 22, 50, 50),
    _p("P-RTM-112G", "112G retimer", "RETIMER", "SUP-09", 70, 20, 100, 100),
    _p("P-PHY-100G", "100G PHY", "PHY", "SUP-09", 38, 18, 100, 100),
    _p("P-PHY-1G", "1G Ethernet PHY", "PHY", "SUP-09", 6.5, 14, 250, 250),
    _p("P-CAGE-OSFP", "OSFP cage", "CAGE", "SUP-07", 18, 12, 100, 100),
    _p("P-CAGE-QSFPDD", "QSFP-DD cage", "CAGE", "SUP-07", 14, 12, 100, 100),
    _p("P-CAGE-SFP", "SFP/SFP+ cage", "CAGE", "SUP-07", 3.2, 10, 500, 500),
    _p("P-CAGE-RJ45", "RJ45 jack", "CAGE", "SUP-07", 2.4, 10, 500, 500),
    _p("P-CHS-2U", "2U chassis", "CHASSIS", "SUP-11", 145, 8, 20, 20),
    _p("P-CHS-1U", "1U leaf chassis", "CHASSIS", "SUP-11", 62, 8, 20, 20),
    _p("P-CHS-L1", "1U chassis, ORB-L1", "CHASSIS", "SUP-11", 55, 8, 20, 20),
    _p("P-CHS-M1", "1U chassis, ORB-M1", "CHASSIS", "SUP-11", 38, 8, 20, 20),
    _p("P-HSINK-L", "Large ASIC heatsink", "HEATSINK", "SUP-05", 48, 10, 50, 50),
    _p("P-HSINK-S", "Small ASIC heatsink", "HEATSINK", "SUP-05", 16, 10, 100, 100),
    _p("P-PWRSEQ", "Power sequencing IC", "POWER_IC", "SUP-03", 4.8, 16, 250, 250),
    _p("P-EMMC-8G", "8GB eMMC", "EMMC", "SUP-12", 9, 10, 250, 250),
    _p("P-TPM", "Trusted platform module", "TPM", "SUP-02", 3.6, 14, 250, 250),
    _p("P-CLK-J2", "Standard clock IC", "CLOCK", "SUP-08", 6, 14, 250, 250),
    _p("P-FLASH-BIOS", "BIOS flash", "FLASH", "SUP-12", 2.2, 12, 500, 500),
    _p("P-RTC-BATT", "RTC coin cell", "BATTERY", "SUP-07", 0.6, 6, 1000, 1000),
]  # fmt: skip

# platform -> [(part_id, qty_per)]
BOM: dict[str, list[tuple[str, int]]] = {
    "ORB-S16": [
        ("P-ASIC-102T", 1), ("P-CPU-8C", 1), ("P-BMC-A", 1), ("P-PSU-3000AC", 2),
        ("P-FAN-80", 6), ("P-DDR5-16G", 2), ("P-RTM-224G", 8), ("P-CLK-J1", 1),
        ("P-SSD-240", 1), ("P-PCBA-S16", 1), ("P-CAGE-OSFP", 64), ("P-CHS-2U", 1),
        ("P-HSINK-L", 1), ("P-PWRSEQ", 4),
    ],
    "ORB-S8": [
        ("P-ASIC-51T", 1), ("P-CPU-8C", 1), ("P-BMC-A", 1), ("P-PSU-3000AC", 2),
        ("P-FAN-80", 6), ("P-DDR5-16G", 2), ("P-CLK-J1", 1), ("P-SSD-240", 1),
        ("P-PCBA-S8", 1), ("P-RTM-112G", 8), ("P-CAGE-OSFP", 64), ("P-CHS-2U", 1),
        ("P-HSINK-L", 1), ("P-TPM", 1),
    ],
    "ORB-S4": [
        ("P-CPU-8C", 1), ("P-BMC-A", 1), ("P-CPLD-S4", 1), ("P-PSU-2000AC", 2),
        ("P-FAN-80", 5), ("P-DDR4-8G", 1), ("P-CLK-J1", 1), ("P-SSD-240", 1),
        ("P-PCBA-S4", 1), ("P-ASIC-25T", 1), ("P-CAGE-QSFPDD", 64), ("P-CHS-2U", 1),
        ("P-HSINK-L", 1), ("P-FLASH-BIOS", 1),
    ],
    "ORB-L4": [
        ("P-CPU-4C", 1), ("P-BMC-A", 1), ("P-PSU-2000AC", 2), ("P-FAN-40", 4),
        ("P-DDR4-8G", 1), ("P-CLK-J1", 1), ("P-SSD-240", 1), ("P-PCBA-L4", 1),
        ("P-ASIC-12T", 1), ("P-CAGE-QSFPDD", 8), ("P-CAGE-SFP", 48), ("P-CHS-1U", 1),
        ("P-HSINK-S", 1), ("P-PHY-100G", 6),
    ],
    "ORB-L1": [
        ("P-CPU-4C", 1), ("P-BMC-A", 1), ("P-PSU-650AC", 1), ("P-FAN-40", 3),
        ("P-DDR4-8G", 1), ("P-PCBA-L1", 1), ("P-ASIC-3T", 1), ("P-CAGE-SFP", 48),
        ("P-CHS-L1", 1), ("P-HSINK-S", 1), ("P-PHY-100G", 6), ("P-EMMC-8G", 1),
        ("P-CLK-J2", 1), ("P-RTC-BATT", 1),
    ],
    "ORB-M1": [
        ("P-CPU-4C", 1), ("P-BMC-A", 1), ("P-PSU-650AC", 1), ("P-FAN-40", 2),
        ("P-DDR4-8G", 1), ("P-PCBA-M1", 1), ("P-ASIC-MGT", 1), ("P-CAGE-RJ45", 48),
        ("P-CAGE-SFP", 4), ("P-CHS-M1", 1), ("P-PHY-1G", 6), ("P-EMMC-8G", 1),
        ("P-CLK-J2", 1), ("P-PWRSEQ", 2),
    ],
}  # fmt: skip

# (part_id, alt_part_id, qualification_status, qual_lead_time_weeks, notes)
ALTERNATES: list[tuple[str, str, str, int, str]] = [
    ("P-BMC-A", "P-BMC-B", "NOT_STARTED", 12,
     "Next-gen BMC; firmware port and 12-week qualification needed"),
    ("P-PSU-2000AC", "P-PSU-3000AC", "QUALIFIED", 0, "Higher-rated PSU is a drop-in; costs more"),
    ("P-CPU-4C", "P-CPU-8C", "IN_QUAL", 8, "8-core module under thermal validation on leaf"),
]  # fmt: skip

# Parts that start the base snapshot with tight cover (weeks); scenarios build on these.
TIGHT_COVERAGE_WEEKS: dict[str, float] = {
    "P-ASIC-102T": 3.0,
    "P-RTM-224G": 3.2,
    "P-DDR5-16G": 3.5,
}

# Annual spares growth until the last-ship year, then decline.
SERVICE_GROWTH_PER_YEAR = 0.10
SERVICE_DECLINE_PER_YEAR = 0.08

NOTES_PLAIN = [
    "wafer allocation tight, partial ship",
    "carrier capacity constrained",
    "confirmed, ships on schedule",
    "pending customs clearance",
    "yield recovering, volume ramping",
    "line maintenance next week, may shift by days",
    "full quantity confirmed",
    "ocean freight booked",
]

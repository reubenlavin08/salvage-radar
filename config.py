"""Configuration: paths, search terms, salvage values, geo tiers, decision rules."""
from pathlib import Path
import os

CODE_DIR = Path(__file__).parent
STATE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "cl_watcher"
STATE_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = STATE_DIR / "state.db"
LOG_DIR = STATE_DIR / "log"
LOG_DIR.mkdir(exist_ok=True)
ENV_PATH = STATE_DIR / ".env"

# Load .env (in STATE_DIR) so EMAIL_* env vars resolve below
try:
    from dotenv import load_dotenv as _load_dotenv
    if ENV_PATH.exists():
        _load_dotenv(ENV_PATH)
except ImportError:
    pass

# Email sender / recipient — read from env vars; falls back to GMAIL_USER
# (which is required in .env anyway). Override either with CL_EMAIL_TO
# or CL_EMAIL_FROM if you want notifications to go somewhere else.
EMAIL_TO = (os.environ.get("CL_EMAIL_TO")
            or os.environ.get("GMAIL_USER") or "")
EMAIL_FROM = (os.environ.get("CL_EMAIL_FROM")
              or os.environ.get("GMAIL_USER") or "")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))

CL_BASE = "https://vancouver.craigslist.org"
CL_FREE_SEARCH = CL_BASE + "/search/zip"
CL_GENERAL_SEARCH = CL_BASE + "/search/sss"
MAX_PAID_PRICE = 30
RSS_FORMAT = "rss"

SEARCH_TERMS = [
    # Original 17
    "printer", "scanner", "treadmill", "power supply", "atx",
    "arduino", "raspberry pi", "electronics", "motor", "stepper",
    "esc", "rc car", "drone", "3d printer", "soldering",
    "multimeter", "laptop",
    # Personal mobility — hub motors, big batteries, controllers
    "e-scooter", "electric scooter", "e-bike", "electric bike",
    "hoverboard", "electric skateboard",
    # Sensor / lidar / SBC sources
    "roomba", "robot vacuum", "kinect", "router", "webcam",
    # Mechanical motor + gearbox sources
    "drill", "power tool", "wheelchair", "cnc", "vcr",
    # Niche but high-value
    "esp32", "servo", "electronics lot",
]

# keyword (lowercased) -> (low, high) CAD salvage estimate
SALVAGE_TABLE = {
    "laser printer": (50, 100),
    "inkjet printer": (40, 70),
    "printer": (40, 60),
    "flatbed": (25, 35),
    "scanner": (20, 35),
    "treadmill": (100, 250),
    "atx": (30, 50),
    "bench psu": (80, 150),
    "bench power": (80, 150),
    "power supply": (25, 45),
    "wall adapter": (5, 15),
    "wall wart": (5, 15),
    "psu": (25, 45),
    "raspberry pi 5": (80, 130),
    "raspberry pi 4": (60, 100),
    "raspberry pi zero": (15, 30),
    "raspberry pi": (40, 80),
    "arduino": (10, 25),
    "uno": (15, 25),
    "esp32": (10, 20),
    "esp8266": (8, 15),
    "nema 17": (12, 18),
    "nema17": (12, 18),
    "nema 23": (20, 30),
    "nema23": (20, 30),
    "stepper": (10, 15),
    "brushless": (20, 40),
    "esc": (15, 30),
    "rc car": (30, 80),
    "quadcopter": (40, 100),
    "drone": (40, 100),
    "ender": (100, 200),
    "3d printer": (80, 200),
    "soldering station": (40, 80),
    "soldering iron": (25, 50),
    "soldering": (20, 40),
    "fluke": (60, 200),
    "multimeter": (15, 60),
    "oscilloscope": (80, 250),
    "scope": (60, 200),
    "laptop": (50, 150),
    "lot of": (15, 50),
    "electronics lot": (30, 80),
    "parts lot": (30, 80),
    "electronics": (10, 30),
    # Personal mobility
    "electric scooter": (100, 250),
    "e-scooter": (100, 250),
    "escooter": (100, 250),
    "electric bike": (150, 400),
    "e-bike": (150, 400),
    "ebike": (150, 400),
    "hoverboard": (80, 150),
    "self balance": (80, 150),
    "electric skateboard": (80, 200),
    "vesc": (80, 200),
    # Sensors / SBC / lidar sources
    "roborock": (80, 200),
    "robot vacuum": (60, 150),
    "roomba": (60, 120),
    "neato": (60, 120),
    "kinect": (40, 80),
    "webcam": (5, 15),
    "router": (15, 30),
    # Mechanical motor sources
    "cordless drill": (35, 70),
    "drill": (25, 50),
    "power tool": (25, 60),
    "wheelchair": (100, 300),
    "cnc": (100, 300),
    "vcr": (25, 50),
    "tape deck": (25, 50),
    # Robotics specific
    "servo": (8, 20),
}

REALLY_GOOD_KEYWORDS = [
    "treadmill", "3d printer", "ender", "oscilloscope", "scope",
    "bench psu", "bench power", "fluke", "raspberry pi 4",
    "raspberry pi 5", "laser printer", "lot of", "electronics lot",
    "parts lot",
    # New high-value categories
    "hoverboard", "electric scooter", "e-scooter", "escooter",
    "electric bike", "e-bike", "ebike",
    "electric skateboard", "vesc",
    "roborock", "robot vacuum", "kinect",
    "wheelchair", "cnc",
]

TIER_A = [
    "dunbar", "west point grey", "point grey", "ubc",
    "university endowment", "uel", "kerrisdale",
]
TIER_B = [
    "kitsilano", "kits", "arbutus", "arbutus ridge",
    "mackenzie heights", "shaughnessy", "south granville",
]
TIER_C = [
    "oakridge", "marpole", "fairview", "south cambie", "cambie",
]
TIER_D = [
    "mount pleasant", "mt pleasant", "main and broadway",
]

HARD_EXCLUDE = [
    "north van", "north vancouver", "lonsdale", "deep cove", "lynn valley",
    "west van", "west vancouver", "ambleside", "dundarave", "horseshoe bay",
    "burnaby", "metrotown", "brentwood", "lougheed",
    "richmond", "steveston",
    "surrey", "newton", "guildford", "fleetwood", "cloverdale",
    "coquitlam", "port coquitlam", "port moody", "tri-cities", "tri cities",
    "new westminster", "new west",
    "delta", "ladner", "tsawwassen",
    "maple ridge", "pitt meadows",
    "langley", "abbotsford", "chilliwack",
    "white rock", "mission", "squamish", "whistler",
    "east van", "east vancouver", "hastings sunrise", "renfrew",
    "commercial drive", "the drive", "strathcona", "grandview",
    "killarney", "victoria-fraserview", "champlain heights",
    "sunset", "south vancouver",
]

TIER_WEIGHTS = {"A": 1.25, "B": 1.10, "C": 1.00, "D": 0.85,
                "unknown": 0.95, "needs_review": 0.90}

# Dunbar-area centerpoint (~ 41st & Dunbar)
DUNBAR_LAT = 49.245
DUNBAR_LON = -123.185

# Distance-based tiers (km from Dunbar centerpoint)
TIER_A_KM = 2.5   # Dunbar core, Pt Grey, UBC east, Kerrisdale W
TIER_B_KM = 4.5   # Kits, full Pt Grey/UBC, Kerrisdale, Arbutus, mid Shaughnessy
TIER_C_KM = 7.0   # Oakridge, Marpole, Fairview, S Cambie
TIER_D_KM = 9.0   # Cambie corridor stretch zone — beyond this = EXCLUDE

# East boundary: Cambie meridian. Listings east of this AND > 4 km from
# Dunbar are excluded regardless of tier ring.
EAST_BOUNDARY_LON = -123.114
EAST_BOUNDARY_TOLERANCE_KM = 4.0

# --- Salvage scorer modifiers ---

# Title contains any of these → not a sell listing, salvage = 0
BUYER_TITLE_PATTERNS = [
    "wanted", "want to buy", "looking for", "iso ", "in search of",
    "for trade", "trade only", "willing to trade", "wtb",
]

# Per-product-class TITLE kill words. If primary product is the key and the
# title contains any of these tokens, salvage = 0 (the listing is selling
# the accessory, not the unit).
NEGATIVE_TITLE_KILLERS = {
    "printer":   ["ink", "toner", "cartridge", "filament", "paper",
                  "stand", "rack", "tray", "cable", "cord", "usb",
                  "ribbon", "head", "fuser"],
    "laser printer": ["toner", "cartridge", "drum"],
    "3d printer": ["filament", "spool", "nozzle", "hotend", "bed only"],
    "ender":     ["filament", "spool", "nozzle"],
    "scanner":   ["scanner ink", "lid"],
    "laptop":    ["bag", "case", "stand", "sleeve", "screen", "battery",
                  "charger", "adapter", "ac adapter", "power brick",
                  "skin", "decal", "lock", "cable", "cord", "security",
                  "accessory", "accessories", "parts", "fan", "hinge",
                  "key", "keyboard", "mount", "riser", "tray", "desk",
                  "fax", "ribbon",
                  # Internal components vs the laptop itself
                  "ram", "memory", "stick", "ssd", "hdd", "drive",
                  "hard drive", "msata", "sata", "nvme", "ddr", "ddr2",
                  "ddr3", "ddr4", "ddr5",
                  # Furniture-style accessories
                  "table", "holder", "padded", "rack", "shelf"],
    "raspberry pi": ["case", "screen", "monitor", "keyboard", "mouse",
                     "fan", "heatsink"],
    "drone":     ["case", "battery", "controller", "props", "blades",
                  "propellers", "remote", "control", "transmitter",
                  "charger", "manual", "bag"],
    "treadmill": ["belt", "console", "manual"],
    "electric bike": ["battery", "tire", "tube", "helmet", "lock", "pedals",
                      "rack", "fender"],
    "e-bike":    ["battery", "tire", "tube", "helmet", "lock", "pedals",
                  "rack", "fender"],
    "ebike":     ["battery", "tire", "tube", "helmet", "lock", "pedals"],
    "electric scooter": ["tire", "tube", "battery", "helmet", "rack"],
    "e-scooter": ["tire", "tube", "battery", "helmet"],
    "drill":     ["drill bit", "bits", "bits set", "press",
                  "high speed steel", "hss", "carbide", "twist",
                  "spade", "auger", "masonry", "tap"],
    "soldering": ["solder", "wire", "flux", "tip"],
    "soldering iron": ["tip", "tips", "wire", "solder", "flux"],
    "motor":     ["oil", "cycle", "home", "outboard", "boat", "trolling"],
    "stepper":   ["bottle", "exercise", "machine"],
    "esc":       ["escrow"],
    "rc car":    ["body", "shell", "tires", "wheels"],
    "vcr":       ["tapes", "tape only", "vhs only", "cable", "cord",
                  "rca", "composite", "audio", "video composite"],
    "tape deck": ["tapes", "tape only", "cable", "cord"],
    "kinect":    ["adapter", "stand", "mount", "game", "games",
                   "disc", "title", "central", "experience",
                   "wipeout", "dance"],
    "router":    ["bits", "table only"],
    "vesc":      ["case", "mount"],
    "hoverboard": ["bag", "cover", "case"],
    "wheelchair": ["cushion", "tire", "wheel"],
    "servo":     ["horn", "horns", "arm only"],
    "webcam":    ["mount", "stand"],
}

# Per-product-class body patterns that mean "accessory only" or "wrong class".
# If primary product is the dict key and body matches any pattern, salvage = 0.
NEGATIVE_BODY_FILTERS = {
    "printer": ["ink only", "ink cartridge", "toner only", "paper only",
                "just ink", "just toner", "ink for", "cartridges for",
                "filament only"],
    "3d printer": ["filament only", "spool only", "spare parts only",
                   "nozzle only", "hotend only"],
    "ender": ["filament only", "nozzle only"],
    "laptop": ["laptop bag", "laptop case", "laptop stand", "laptop sleeve",
               "screen replacement", "battery only", "charger only",
               "power brick only", "ac adapter only"],
    "electric bike": ["battery only", "tire only", "tube only", "helmet",
                      "lock only", "pedals only"],
    "e-bike": ["battery only", "tire only", "tube only", "helmet",
               "lock only", "pedals only"],
    "ebike": ["battery only", "tire only", "tube only", "helmet"],
    "drone": ["case only", "battery only", "controller only",
              "propellers only", "props only", "parts only"],
    "raspberry pi": ["case only", "screen only", "monitor only",
                     "keyboard only"],
    "treadmill": ["belt only", "console only", "manual only"],
    "scanner": ["scanner glass", "lid only", "scanner ink"],
    "drill": ["drill bit", "drill bits", "drill press only"],
    "soldering": ["soldering wire only", "solder only", "flux only"],
    "soldering iron": ["soldering wire only", "solder only"],
    "stepper": ["stepper bottle", "stepper exercise"],
    "motor": ["motor oil", "motor cycle", "motorcycle", "motor home",
              "motorhome", "outboard motor"],
    "electronics": ["cigarette", "vape"],
    "kinect": ["for kinect", "video game", "game disc", "compatible with kinect"],
}

HEAVY_NEGATIVE_MODIFIERS = [
    "missing", "no power", "won't turn on", "doesn't power on",
    "untested", "as-is", "as is", "no testing", "stripped",
    "water damage", "fire damage",
]
LIGHT_NEGATIVE_MODIFIERS = [
    "broken", "doesn't work", "not working", "for parts",
    "for parts only", "needs repair", "needs work",
]
POSITIVE_MODIFIERS = [
    "working", "tested", "perfect condition", "like new",
    "with charger", "with cables", "with accessories", "complete",
    "all working", "fully working", "great condition",
]

BRAND_BOOST = {
    # Only true brand premiums — don't double up with SALVAGE_TABLE entries
    "fluke": 2.0,           # multimeter premium
    "tektronix": 1.8,       # oscilloscope premium
    "rigol": 1.6,           # oscilloscope premium
    "klipper": 1.3,         # 3d printer firmware → high-quality build
    "voron": 1.5,           # premium 3d printer brand
    "lidar": 1.5,           # robot vacuum with lidar = premium
    "prusa": 1.4,           # premium 3d printer brand
}

# --- end salvage modifiers ---

# Patterns that indicate an unreliable listed price (real price unknown)
PRICE_UNKNOWN_PATTERNS = [
    "make me an offer", "make an offer", "make offer", "best offer",
    "obo", "or best offer", "open to offers", "message for price",
    "msg for price", "dm for price", "pm for price", "name your price",
    "negotiable", "or trade", "for trade",
]

REQUEST_DELAY_SEC = 1.5
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0"

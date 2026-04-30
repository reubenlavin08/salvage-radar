"""Appraiser config: paths, model choices, thresholds, eBay backend
selection, AND the user's full salvage criteria (category table, tiers,
brand boosts, kill list).

The appraiser is its own module. It reads listings read-only from
cl_watcher's SQLite DB and writes appraisal results into its own DB so
the watcher's data is never modified.
"""
from __future__ import annotations
from pathlib import Path
import os

CODE_DIR = Path(__file__).parent
STATE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "cl_watcher"
APPRAISAL_DIR = STATE_DIR / "appraiser"
APPRAISAL_DIR.mkdir(parents=True, exist_ok=True)

SOURCE_DB_PATH = STATE_DIR / "state.db"            # cl_watcher (read-only)
APPRAISAL_DB_PATH = APPRAISAL_DIR / "appraisal.db"
COMPS_CACHE_PATH = APPRAISAL_DIR / "comps_cache.db"
LOG_DIR = APPRAISAL_DIR / "log"
LOG_DIR.mkdir(exist_ok=True)
ENV_PATH = STATE_DIR / ".env"  # shares secrets with cl_watcher

# ---------- Model selection ----------
TRIAGE_MODEL = "claude-haiku-4-5-20251001"
EXTRACT_MODEL = "claude-sonnet-4-6"
VALUATE_MODEL = "claude-sonnet-4-6"

TRIAGE_MAX_TOKENS = 400
EXTRACT_MAX_TOKENS = 1500
VALUATE_MAX_TOKENS = 1200

# ---------- Pipeline thresholds ----------
TRIAGE_RATIO_FLOOR = 1.5
TRIAGE_CONFIDENCE_FLOOR = "low"   # one of: low, medium, high
RECOMMEND_RATIO = 2.5
SALVAGE_REALIZATION_FACTOR = 0.70

# ---------- eBay backend ----------
# 'scrape' | 'api' | 'mock'
EBAY_BACKEND = os.environ.get("APPRAISER_EBAY_BACKEND", "scrape")
EBAY_MAX_COMPS_PER_QUERY = 20
EBAY_CACHE_TTL_DAYS = 14
EBAY_REQUEST_DELAY_S = 1.5
EBAY_TIMEOUT_S = 20

TRIAGE_CONCURRENCY = 8
EXTRACT_CONCURRENCY = 4
COMPS_CONCURRENCY = 3

# =============================================================
#                      USER CRITERIA
# Mirrors the table the user provided. Edit here to retune.
# =============================================================

# ---------- Component taxonomy used by the extractor ----------
COMPONENT_CATEGORIES = [
    "gpu", "cpu", "motherboard", "ram", "psu", "ssd", "hdd",
    "laptop_battery", "laptop_screen", "laptop_keyboard",
    "stepper_motor", "servo_motor", "brushless_motor", "dc_motor",
    "lithium_battery_pack", "battery_18650_cell",
    "single_board_computer", "microcontroller", "esc", "vesc",
    "lidar", "camera_module", "kinect_sensor",
    "lcd_panel", "ribbon_cable", "linear_rail", "leadscrew", "ball_bearing",
    "hotend", "extruder", "build_plate",
    "power_brick", "transformer", "solenoid", "relay",
    "soldering_station", "multimeter", "oscilloscope", "bench_psu",
    "tool_battery", "drill", "saw_blade",
    "hub_motor", "ebike_battery", "ebike_controller",
    "router_board", "webcam", "speaker_driver",
    "encoder", "ir_sensor", "cliff_sensor", "imu",
    "other",
]

# ---------- Category-of-interest table ----------
# Mirrors the user's table. Each row encodes:
#   reason              : why they want it
#   really_good         : passes the $21–$30 + 3× rule
#   typical_components  : comma list, used to seed extraction hints
CATEGORY_TABLE: dict[str, dict] = {
    "printer":               {"reason": "steppers, linear rails, belts, gears, regulated PSU",
                              "really_good": False,
                              "typical_components": "stepper_motor,linear_rail,psu,dc_motor"},
    "laser printer":         {"reason": "above + bigger PSU + extra motors",
                              "really_good": True,
                              "typical_components": "stepper_motor,psu,dc_motor,solenoid"},
    "flatbed scanner":       {"reason": "stepper + smooth linear rail",
                              "really_good": False,
                              "typical_components": "stepper_motor,linear_rail"},
    "treadmill":             {"reason": "big DC motor 1+ hp, controller, frame",
                              "really_good": True,
                              "typical_components": "dc_motor,ebike_controller"},
    "atx psu":               {"reason": "bench-style 12V/5V/3.3V supply",
                              "really_good": False,
                              "typical_components": "psu"},
    "bench psu":             {"reason": "variable lab supply",
                              "really_good": True,
                              "typical_components": "bench_psu"},
    "oscilloscope":          {"reason": "lab measurement",
                              "really_good": True,
                              "typical_components": "oscilloscope"},
    "multimeter":            {"reason": "workshop staple",
                              "really_good": False,
                              "typical_components": "multimeter"},
    "soldering station":     {"reason": "workshop staple",
                              "really_good": False,
                              "typical_components": "soldering_station"},
    "arduino":               {"reason": "cheap compute",
                              "really_good": False,
                              "typical_components": "microcontroller"},
    "esp32":                 {"reason": "cheap WiFi/BT microcontroller",
                              "really_good": False,
                              "typical_components": "microcontroller"},
    "raspberry pi":          {"reason": "Linux SBC",
                              "really_good": False,
                              "typical_components": "single_board_computer"},
    "raspberry pi 4":        {"reason": "better SBC",
                              "really_good": True,
                              "typical_components": "single_board_computer"},
    "raspberry pi 5":        {"reason": "better SBC",
                              "really_good": True,
                              "typical_components": "single_board_computer"},
    "stepper":               {"reason": "motion-control actuator",
                              "really_good": False,
                              "typical_components": "stepper_motor"},
    "brushless motor":       {"reason": "RC / robotics actuator",
                              "really_good": False,
                              "typical_components": "brushless_motor"},
    "esc":                   {"reason": "brushless motor driver",
                              "really_good": False,
                              "typical_components": "esc"},
    "servo":                 {"reason": "robotics actuator",
                              "really_good": False,
                              "typical_components": "servo_motor"},
    "rc car":                {"reason": "brushless + ESC + battery + chassis",
                              "really_good": False,
                              "typical_components": "brushless_motor,esc,lithium_battery_pack"},
    "drone":                 {"reason": "brushless + ESC + LiPo + IMU + camera",
                              "really_good": False,
                              "typical_components": "brushless_motor,esc,lithium_battery_pack,imu,camera_module"},
    "3d printer":            {"reason": "steppers, hotend, controller, rails",
                              "really_good": True,
                              "typical_components": "stepper_motor,hotend,linear_rail,extruder"},
    "hoverboard":            {"reason": "2 hub motors + big Li-ion + gyro/IMU board",
                              "really_good": True,
                              "typical_components": "hub_motor,lithium_battery_pack,imu,ebike_controller"},
    "electric scooter":      {"reason": "hub motor, controller, big battery",
                              "really_good": True,
                              "typical_components": "hub_motor,ebike_controller,ebike_battery"},
    "electric bike":         {"reason": "hub motor, controller, bigger battery",
                              "really_good": True,
                              "typical_components": "hub_motor,ebike_controller,ebike_battery"},
    "electric skateboard":   {"reason": "outrunner motors, premium ESC, battery",
                              "really_good": True,
                              "typical_components": "brushless_motor,vesc,lithium_battery_pack"},
    "vesc":                  {"reason": "premium brushless controller",
                              "really_good": True,
                              "typical_components": "vesc"},
    "robot vacuum":          {"reason": "encoders, IR/cliff sensors, dock; lidar on premium",
                              "really_good": True,
                              "typical_components": "encoder,ir_sensor,cliff_sensor,lidar"},
    "kinect":                {"reason": "depth + RGB camera + IR projector",
                              "really_good": True,
                              "typical_components": "kinect_sensor,camera_module"},
    "webcam":                {"reason": "UVC USB camera",
                              "really_good": False,
                              "typical_components": "webcam"},
    "router":                {"reason": "OpenWrt-able SBC",
                              "really_good": False,
                              "typical_components": "router_board"},
    "drill":                 {"reason": "brushed motor + planetary gearbox",
                              "really_good": False,
                              "typical_components": "dc_motor,tool_battery"},
    "wheelchair":            {"reason": "high-torque motors + gearbox + joystick",
                              "really_good": True,
                              "typical_components": "dc_motor,lithium_battery_pack,ebike_controller"},
    "cnc":                   {"reason": "steppers, drivers, linear rails",
                              "really_good": True,
                              "typical_components": "stepper_motor,linear_rail,leadscrew"},
    "lathe":                 {"reason": "steppers, drivers, linear rails",
                              "really_good": True,
                              "typical_components": "stepper_motor,linear_rail,leadscrew"},
    "mill":                  {"reason": "steppers, drivers, linear rails",
                              "really_good": True,
                              "typical_components": "stepper_motor,linear_rail,leadscrew"},
    "vcr":                   {"reason": "precision motors, gears, belts",
                              "really_good": False,
                              "typical_components": "dc_motor"},
    "laptop":                {"reason": "Linux compute + battery + screen",
                              "really_good": False,
                              "typical_components": "laptop_battery,laptop_screen,ssd,ram"},
    "electronics lot":       {"reason": "mixed bag, variable",
                              "really_good": True,
                              "typical_components": "other"},
}

# Excluded categories — never appraise these.
EXCLUDED_CATEGORIES = [
    "bicycle", "bike (non-electric)",
    "office chair", "desk chair",
    "crt tv", "tube tv",
    "used lithium battery (loose, unknown provenance)",
]

# ---------- Brand boost ----------
# Premium brands within a category get a salvage boost (×1.3–2.0). The
# multiplier is capped in the valuator.
BRAND_BOOST: dict[str, float] = {
    "fluke": 2.0,
    "rigol": 1.7, "siglent": 1.5, "keysight": 2.0, "tektronix": 1.8,
    "weller": 1.5, "hakko": 1.6, "metcal": 1.8,
    "anycubic": 1.3, "prusa": 2.0, "bambu": 2.0, "bambulab": 2.0,
    "creality": 1.3, "ender": 1.3,
    "irobot": 1.4, "roomba": 1.4, "neato": 1.3,
    "boosted": 2.0, "evolve": 1.7, "meepo": 1.4,
    "dji": 1.7, "parrot": 1.4,
    "traxxas": 1.5, "arrma": 1.4,
    "permobil": 1.6, "quickie": 1.4,
    "haas": 1.8, "tormach": 1.6, "shapeoko": 1.4, "carbide3d": 1.4,
    "milwaukee": 1.4, "dewalt": 1.4, "makita": 1.4, "bosch": 1.3,
    "thinkpad": 1.3, "lenovo thinkpad": 1.4,
    "raspberry pi": 1.2,
    "ti-": 1.3,  # TI stepper drivers, dev boards
    "trinamic": 1.5,
}

# ---------- Condition multipliers ----------
# Used by the LLM and by deterministic fallback. Source: user's table.
CONDITION_MULTIPLIERS = {
    "new": 1.25, "like new": 1.20, "excellent": 1.15,
    "good": 1.00, "fair": 0.85,
    "salvage": 0.50, "parts only": 0.50, "for parts": 0.50,
}

# Body-keyword multipliers (used only if no structured condition attribute)
BODY_POSITIVE_MULT = 1.15
BODY_LIGHT_NEG_MULT = 0.70
BODY_HEAVY_NEG_MULT = 0.50
BODY_POSITIVE_KEYWORDS = ["working", "tested", "like new"]
BODY_LIGHT_NEG_KEYWORDS = ["broken", "for parts", "parts only"]
BODY_HEAVY_NEG_KEYWORDS = ["missing", "no power", "untested"]

QUANTITY_CAP = 3   # ×min(N, 3)

# ---------- Coarse non-electronics drop list ----------
# Hard exclusions: if a title hits one of these, drop the listing
# UNLESS a KEEP_OVERRIDE keyword also appears (mechanical salvage
# escape hatch — gears, CNC frame, linear rails, etc.).
NON_ELECTRONICS_KEYWORDS: list[str] = [
    # Recorded media (the album, not the player)
    "vinyl", "vinyls", "lp record", "lp records", "33 rpm", "45 rpm",
    "cassette tape", "cassette tapes", "vhs tape", "vhs tapes",
    "vhs movie", "vhs movies", "vhs collection",
    "dvd movie", "dvd movies", "dvd collection",
    "cd album", "cd albums", "music cds", "cd collection",
    "blu-ray movie", "blu ray movie", "blu-ray collection",
    # Jewelry / precious metals
    "sterling silver", "jewelry", "jewellery", "necklace", "pendant",
    "earring", "earrings", "bracelet", "diamond ring", "gold chain",
    "engagement ring", "wedding ring", "wedding band",
    # Clothing / footwear
    "clothing", "sneakers", "shoes", "boots", "jacket", "coat",
    "pants", "jeans", " shirt", "dress shirt", "skirt", "scarf",
    "hoodie", "sweater", "wedding dress", "tuxedo", "suit jacket",
    "bra ", "bras ", "lingerie",
    # Furniture
    "office chair", "desk chair", "sofa", "couch", "loveseat",
    "mattress", "bed frame", "bedframe", "bookshelf", "bookcase",
    "dresser", "nightstand", "wardrobe", "armchair", "ottoman",
    "coffee table", "dining table", "side table", "futon",
    "kitchen table", "end table",
    # ("tv stand" deliberately omitted — TVs often listed with their
    # stand and the TV itself is salvageable.)
    # Books / art / media print
    " book ", "books for sale", "paperback", "hardcover", "novel",
    "comic book", "magazine", "magazines", "poster", "posters",
    "painting", "artwork", "picture frame", "art print",
    "encyclopedia", "textbook",
    # Kitchenware
    "cookware", "dishware", "dinnerware", "glassware", "tupperware",
    "pots and pans", "pots & pans", "frying pan", "saucepan",
    "kitchen knives", "cutlery set", "wine glasses", "mugs",
    # Pure hand tools (no salvage value to robotics)
    "hammer", "screwdriver set", "hand saw", "wrench set", "pliers set",
    "tape measure", "level tool", "tool box", "wrench",
    "screwdriver only", "socket set",
    # Toys (RC / drone exception handled via KEEP_OVERRIDE)
    "stuffed animal", "stuffed toy", "doll ", "doll house",
    "lego ", "lego set", "board game", "card game",
    "jigsaw puzzle", "trading cards", "pokemon cards", "action figure",
    "collectible figure",
    # Sports / fitness (electric mobility handled via KEEP_OVERRIDE)
    "mountain bike", "road bike", "bike helmet", "skis ", "snowboard",
    "hockey stick", "weights set", "dumbbell", "kettlebell", "yoga mat",
    "tennis racket", "golf club", "golf bag", "baseball glove",
    "fishing rod", "fishing reel", "fishing tackle", "tackle box",
    "ski boots", "snowboard boots", "skates ",
    # Decorative / soft home
    "candle", "vase", "rug", "carpet", "curtain", "drapes",
    "houseplant", "plant pot", "throw pillow", "tablecloth",
    "wall hanging", "decor only", "ornament",
    # Live plants / aquarium plants (no electronics)
    "live plant", "succulent", "aquarium plant", "aquarium plants",
    "potted plant", "fish tank plant", "terrarium plant",
    # Misc personal
    "handbag", "purse", "wallet", "perfume", "cosmetic", "makeup",
    "skincare", "shampoo",
    # Building materials / yard / outdoor
    "lumber", "plywood", "drywall", "concrete", "gravel", "topsoil",
    "mulch", "garden soil", "fence panel",
    "gas can", "propane tank", "kerosene",
    # Vehicle parts (without electronics — KEEP_OVERRIDE handles e-bikes etc.)
    "tire only", "tires set", "rim set", "wheel cover",
    "brake pad", "brake rotor", "muffler", "exhaust pipe",
    "bumper only", "fender only",
    # Holiday/seasonal — costume only, NOT generic decorations
    # (decorations and "christmas tree" are too risky: pre-lit trees,
    # animatronic props, LED yard signs all exist).
    "halloween costume", "christmas ornament",
    # Services / non-tangible
    " lessons", "tutoring", "consultation", "appointment",
    " massage", "service offered",
]

# Override: if the title or first 500 chars of body contain any of these
# phrases, the non-electronics filter does NOT drop the listing. This is
# the mechanical-salvage escape hatch: scrapped CNC frames, linear rails,
# gear sets, premium-brand listings, etc.
KEEP_OVERRIDE_KEYWORDS: list[str] = [
    # Compound motor phrases (avoid bare "motor" — too generic)
    "stepper motor", "servo motor", "brushless motor", "dc motor",
    "hub motor", "outrunner", "linear actuator",
    # Mechanical salvage
    "linear rail", "lead screw", "leadscrew", "ball screw",
    "ball bearing", "thrust bearing", "tapered bearing",
    "planetary gear", "gear set", "gearbox", "timing belt",
    "drive belt", "v-belt", "pulley", "sprocket", "chain drive",
    # Equipment whose frame/parts you'd want
    "cnc", " lathe", " mill ", "milling machine", "shaper",
    "treadmill",  # already a category but reinforcing
    # Strong parts-listing signals
    "scrapped", "dismantled", "for parts", "parts only", "salvage",
    "rebuild project",
    # Premium brands worth keeping
    "fluke", "bambu", "prusa", "boosted board", "vesc", "fanuc",
    "tektronix", "rigol", "keysight", "siglent",
    # Robotics / SBC keywords
    "raspberry pi", "arduino", "esp32", "esp8266", "kinect",
    "lidar", "imu",
    # Electric mobility (vs. regular bicycle)
    "e-bike", "e-scooter", "e-skateboard", "electric scooter",
    "electric bike", "electric skateboard", "hoverboard",
    # RC / drone (vs. plain toys)
    "rc car", "rc truck", "drone", "quadcopter", "fpv",
    # Animatronic props (have motors/servos/sound modules)
    "animatronic", "animatronics",
    # Lit decorations are electronics
    "led light", "led strip", "fairy light", "string light",
    "pre-lit", "battery operated",
]

# ---------- Kill rules — value forced to $0 ----------
BUYER_TITLE_PATTERNS = [
    "wanted", "wtb", "want to buy", "iso ", " iso", "looking for",
    "for trade", "trade for", "in search of",
]
ACCESSORY_TITLE_KILLERS: dict[str, list[str]] = {
    "printer": ["ink", "ink cartridge", "toner", "filament", "paper"],
    "3d printer": ["filament", "spool", "nozzle"],
    "laptop": ["bag", "case", "sleeve", "charger only", "adapter only"],
    "drone": ["props", "propeller", "battery only"],
    "electric bike": ["battery only", "tire only", "tire", "saddle", "seat"],
    "electric scooter": ["battery only", "tire", "deck only"],
}
ACCESSORY_BODY_KILLERS: list[str] = [
    "battery only", "charger only", "props only", "case only",
    "for the X only",  # template; real list lives in cl_watcher
]

# ---------- Geo / distance ----------
DUNBAR_LAT = 49.245
DUNBAR_LON = -123.185
TIER_A_KM = 2.5    # ×1.25
TIER_B_KM = 4.5    # ×1.10
TIER_C_KM = 7.0    # ×1.00
TIER_D_KM = 9.0    # ×0.85, really-good only
TIER_WEIGHTS = {"A": 1.25, "B": 1.10, "C": 1.00, "D": 0.85}

EAST_BOUNDARY_LON = -123.115         # Cambie meridian
EAST_BOUNDARY_TOLERANCE_KM = 4.0
LIONS_GATE_LAT = 49.32
LIONS_GATE_LON_MAX = -123.05
FRASER_NORTH_ARM_LAT = 49.197

# Hard-cap distance used by the prepare.py prefilter. Any listing with
# coordinates farther than this from Dunbar gets dropped before the LLM
# ever sees it. Listings without coordinates are kept (we can't tell).
MAX_PREFILTER_DISTANCE_KM = 10.0

# ---------- Buy decision rule ----------
# Free + tier in {A,B,C,D-if-really-good} → BUY
# Paid ≤ $20 → BUY if salvage ≥ 2× ask
# Paid $21–$30 → BUY if salvage ≥ 3× ask AND really_good
# Paid > $30 → SKIP (never buy)
PAID_LOW_CEILING = 20
PAID_HIGH_CEILING = 30
PAID_LOW_RATIO = 2.0
PAID_HIGH_RATIO = 3.0

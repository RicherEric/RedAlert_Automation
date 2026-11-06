"""Yuri's Revenge 1.001 (gamemd.exe) memory addresses and structure offsets.

Static addresses: YRpp headers (Phobos-developers/YRpp @ f14642c, 2025-10-25).
Field offsets: MSVC class layouts of the same headers (tools/yr_layout/dump_layout.bat).
Sanity anchors that matched known values: sizeof(TechnoClass)=0x520, UnitClass=0x8E8,
InfantryClass=0x6F0, BuildingClass=0x720, HouseClass=0x160B8, HouseClass::Balance=0x30C.
"""

EXPECTED_PE_TIMESTAMP = 0x3BDF544E  # the Steam gamemd.exe this was verified against

# --- globals -------------------------------------------------------------------
CURRENT_FRAME = 0xA8ED84        # int, game logic frame counter
CURRENT_FRAME_RATE = 0xABCD44   # unsigned int, FPSCounter::CurrentFrameRate
CURRENT_PLAYER = 0xA83D4C       # HouseClass*, the local player's house
GAME_SPEED = 0xA8EB60           # int, GameOptionsClass::Instance.GameSpeed (first field; 3 read in game)

# DynamicVectorClass<T*> instances
HOUSE_ARRAY = 0xA80228
TECHNO_ARRAY = 0xA8EC78
UNIT_ARRAY = 0x8B4108
INFANTRY_ARRAY = 0xA83DE8
BUILDING_ARRAY = 0xA8EB40
AIRCRAFT_ARRAY = 0xA8E390

# --- DynamicVectorClass<T> -----------------------------------------------------
DVC_ITEMS = 4    # T* Items
DVC_COUNT = 16   # int Count

# --- CounterClass (VectorClass<int> + Total) ---------------------------------------
COUNTER_ITEMS = 4
COUNTER_CAPACITY = 8
COUNTER_TOTAL = 16

# --- AbstractTypeClass ----------------------------------------------------------
TYPE_ID = 36          # char ID[25]
TYPE_ID_LEN = 25

# --- HouseClass -----------------------------------------------------------------
HOUSE_ARRAY_INDEX = 48
HOUSE_TYPE = 52               # HouseTypeClass*
HOUSE_AI_DIFFICULTY = 388     # int AIDifficulty: 0 = hard, 1 = normal, 2 = easy (YRpp offsetof, 0x184)
HOUSE_IS_HUMAN = 492          # bool
HOUSE_IS_PLAYER_CONTROL = 493  # bool
HOUSE_DEFEATED = 501          # bool
HOUSE_BALANCE = 780           # int credits (0x30C)
HOUSE_POWER_OUTPUT = 21412
HOUSE_POWER_DRAIN = 21416
HOUSE_FACTORIES = {           # FactoryClass* Primary_For*
    "aircraft": 21420,
    "infantry": 21424,
    "vehicle": 21428,
    "naval": 21432,
    "building": 21436,
    "defense": 21452,         # Primary_ForDefenses (BuildCat=Combat: walls, guns, flak) builds alongside
}
HOUSE_OWNED_COUNTERS = {      # CounterClass, indexed by the type's array index
    "building": 21760,
    "vehicle": 21780,
    "infantry": 21800,
    "aircraft": 21820,
}

# --- ObjectClass / TechnoClass -----------------------------------------------------
OBJ_HEALTH = 108        # int
OBJ_IS_ON_MAP = 116     # bool
OBJ_IN_LIMBO = 129      # bool
OBJ_IS_SELECTED = 131   # bool
OBJ_IS_ALIVE = 144      # bool
OBJ_LOCATION = 156      # CoordStruct {int X, Y, Z} in leptons (256 per cell)
TECHNO_OWNER = 540      # HouseClass*
TECHNO_AMMO = 764       # int
TYPE_PTR = {            # <Unit|Infantry|Building|Aircraft>Class::Type
    "vehicle": 1732,
    "infantry": 1728,
    "building": 1312,
    "aircraft": 1732,
}
OBJECT_ARRAYS = {
    "vehicle": UNIT_ARRAY,
    "infantry": INFANTRY_ARRAY,
    "building": BUILDING_ARRAY,
    "aircraft": AIRCRAFT_ARRAY,
}

# --- FactoryClass ----------------------------------------------------------------
FACTORY_PROGRESS = 36   # StageClass Production; Value at +0
FACTORY_OBJECT = 88     # TechnoClass* being built
FACTORY_ON_HOLD = 92    # bool
FACTORY_OWNER = 108     # HouseClass*
PRODUCTION_STEPS = 54   # StageClass value at completion (verified in game 2026-10-31)

LEPTONS_PER_CELL = 256

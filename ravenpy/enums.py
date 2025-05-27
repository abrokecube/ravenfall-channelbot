from enum import Enum

class ItemTypes(Enum):
    TwoHandedSword = 1
    OneHandedSword = 2
    TwoHandedAxe = 3
    OneHandedAxe = 4
    TwoHandedStaff = 5 
    TwoHandedBow = 6
    TwoHandedSpear = 7
    Helmet = 8
    Chest = 9
    Gloves = 10
    Boots = 11
    Leggings = 12 
    Shield = 13
    LeftShoulder = 14
    RightShoulder = 15
    Ring = 16
    Amulet = 17
    Food = 18
    Potion = 19
    Pet = 20
    Coins = 21
    Woodcutting = 22
    Mining = 23
    Fishing = 24
    Farming = 25
    Arrows = 26
    Magic = 27
    StreamerToken = 28
    Scroll = 29
    Hat = 30
    Mask = 31
    HeadCovering = 32
    Gathering = 33
    Crafting = 34
    Cooking = 35
    Alchemy = 36

class ItemCategory(Enum):
    Weapon = 0
    Armor = 1
    Ring = 2
    Amulet = 3
    Food = 4
    Potion = 5 
    Pet = 6
    Resource = 7
    StreamerToken = 8
    Scroll = 9
    Skin = 10
    Cosmetic = 11

class ItemMaterials(Enum):
    Bronze = 1
    Iron = 2
    Steel = 3
    Black = 4
    Mithril = 5
    Adamantite = 6
    Rune = 7
    Dragon = 8
    Abraxas = 9
    Phantom = 10
    Lionsbane = 11
    Ether = 12
    Ancient = 13
    Atlarus = 14
    ElderBronze = 15
    ElderIron = 16
    ElderSteel = 17
    ElderBlack = 18
    ElderMithril = 19
    ElderAdamantite = 20
    ElderRune = 21
    ElderDragon = 22
    ElderAbraxas = 23
    ElderPhantom = 24
    ElderLionsbane = 25
    ElderEther = 26
    ElderAncient = 27
    ElderAtlarus = 28

class Skills(Enum):
    Attack = 0
    Defense = 1
    Strength = 2
    Health = 3
    Woodcutting = 4
    Fishing = 5
    Mining = 6
    Crafting = 7
    Cooking = 8
    Farming = 9
    Slayer = 10
    Magic = 11
    Ranged = 12
    Sailing = 13
    Healing = 14
    Gathering = 15
    Alchemy = 16
    Melee = 900
    All = 999

class Enchantments(Enum):
    Attack = 0
    Defense = 1
    Strength = 2
    Health = 3
    Woodcutting = 4
    Fishing = 5
    Mining = 6
    Crafting = 7
    Cooking = 8
    Farming = 9
    Slayer = 10
    Magic = 11
    Ranged = 12
    Sailing = 13
    Healing = 14
    Gathering = 15
    Alchemy = 16
    Power = 17
    Aim = 18
    Armor = 19

class Stat(Enum):
    WeaponAim = 0
    WeaponPower = 1
    MagicAim = 2
    MagicPower = 3
    RangedAim = 4
    RangedPower = 5
    ArmorPower = 6

class Effects(Enum):
    NoEffect = 0
    Heal = 1
    Regeneration = 2
    Strength = 3
    Defense = 4
    Dodge = 5
    HitChance = 6
    MovementSpeed = 7
    AttackSpeed = 8
    CastSpeed = 9
    AttackPower = 10
    RangedPower = 11
    MagicPower = 12
    HealingPower = 13
    ExperienceGain = 14
    CriticalHitChance = 15
    InflictPoison = 16
    InflictBleeding = 17
    Burning = 18
    HealthSteal = 19
    Poison = 20
    Bleeding = 21
    Burning2 = 22
    Damage = 23
    ReducedHitChance = 24
    ReducedMovementSpeed = 25
    ReducedAttackSpeed = 26
    ReducedCastSpeed = 27
    IncreasedCriticalHitDamage = 28
    RemoveItem = 29
    AddItem = 30
    Teleport = 31

class Islands(Enum):
    # NoneIsland = -2
    Ferry = -1
    Sailing = 0
    Home = 1
    Away = 2
    Ironhill = 3
    Kyo = 4
    Heim = 5
    Atria = 6
    Eldara = 7

class ClanSkill(Enum):
    Enchanting = 1

class ClanRole(Enum):
    Inactive = 0
    Recruit = 1
    Member = 2
    Officer = 3

class PlayerTask(Enum):
    Woodcutting = 0
    Fishing = 1
    Mining = 2
    Crafting = 3
    Cooking = 4
    Farming = 5
    Gathering = 6
    Brewing = 7
    Fighting = 8

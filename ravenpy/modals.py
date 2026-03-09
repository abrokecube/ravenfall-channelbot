from typing import NamedTuple, TypedDict

class ItemStatJson(TypedDict):
    stat: int
    level: int

class EquipRequirementJson(TypedDict):
    skill: int
    level: int

class CraftIngredientJson(TypedDict):
    item_id: str
    amount: int

class ItemEffectJson(TypedDict):
    id: int
    duration: int
    percentage: float
    min_amount: int

class RFRecipeIngredientJson(TypedDict):
    itemId: str
    amount: int

class RFRecipeJson(TypedDict):
    id: str
    name: str
    description: str | None
    itemId: str
    failedItemId: str | None
    minSuccessRate: int
    maxSuccessRate: int
    preparationTime: int
    fixedSuccessRate: bool
    requiredLevel: int
    requiredSkill: int
    ingredients: list[RFRecipeIngredientJson]

class InternalItemData(TypedDict):
    id: str
    name: str
    description: str | None
    stats: list[ItemStatJson]
    level: int
    equip_requirements: list[EquipRequirementJson]
    type: int
    category: int
    material: int
    sell_price: int
    buy_price: int
    enchantments: int
    soulbound: bool
    modified: str | None
    craft_skill: str | None
    craft_level: int
    min_success_rate: int
    max_success_rate: int
    preperation_time: int
    is_fixed_success_rate: bool
    craft_fail_item: str | None
    craft_ingredients: list[CraftIngredientJson]
    drop_skill: str | None
    drop_level: int
    drop_chance: float
    drop_cooldown: int
    used_in: list[str]
    effects: list[ItemEffectJson]
    raid_drop_month_start: int
    raid_drop_month_length: int
    raid_min_drop: float
    raid_max_drop: float
    raid_drop_tier: int
    drop_slayer_requirement: int

class RFItemJson(TypedDict):
    id: str
    name: str
    description: str
    level: int
    weaponAim: int
    weaponPower: int
    magicAim: int
    magicPower: int
    rangedAim: int
    rangedPower: int
    armorPower: int
    requiredAttackLevel: int
    requiredDefenseLevel: int
    requiredMagicLevel: int
    requiredRangedLevel: int
    requiredSlayerLevel: int
    category: int
    type: int
    material: int
    headMask: int
    maleModelId: str | None
    femaleModelId: str | None
    genericPrefab: str
    malePrefab: str | None
    femalePrefab: str | None
    isGenericModel: bool
    shopBuyPrice: int
    shopSellPrice: int
    soulbound: bool
    modified: str

class RFItemDropJson(TypedDict):
    itemId: str
    requiredSkill: int
    levelRequirement: int
    dropChance: float
    cooldown: int

class RFItemRedeemableJson(TypedDict):
    id: str
    itemId: str
    currencyItemId: str
    cost: int
    amount: int
    availableDateRange: str | None

class FuzzResult(NamedTuple):
    string: str
    score: int
    
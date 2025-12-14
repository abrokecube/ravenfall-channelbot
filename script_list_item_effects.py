import asyncio
import ravenpy
from ravenpy import Item, Effects
from utils.format_time import format_seconds, TimeSize
from utils.strutils import strjoin

status_effect_names = {
    Effects.NoEffect: "No effect",
    Effects.Heal: "HP",
    Effects.Regeneration: "Regeneration",
    Effects.Strength: "Strength",
    Effects.Defense: "Defense",
    Effects.Dodge: "Dodge",
    Effects.HitChance: "Hit chance",
    Effects.MovementSpeed: "Movement speed",
    Effects.AttackSpeed: "Attack speed",
    Effects.CastSpeed: "Cast speed",
    Effects.AttackPower: "Attack power",
    Effects.RangedPower: "Ranged power",
    Effects.MagicPower: "Magic power",
    Effects.HealingPower: "Healing power",
    Effects.ExperienceGain: "Experience gain",
    Effects.CriticalHitChance: "Critical hit chance",
    Effects.InflictPoison: "Inflict poison",
    Effects.InflictBleeding: "Inflict bleeding",
    Effects.Burning: "Burning",
    Effects.HealthSteal: "Health steal",
    Effects.Poison: "Poison",
    Effects.Bleeding: "Bleeding",
    Effects.Burning2: "Burning",
    Effects.Damage: "Damage",
    Effects.ReducedHitChance: "Reduced hit chance",
    Effects.ReducedMovementSpeed: "Reduced movement speed",
    Effects.ReducedAttackSpeed: "Reduced attack speed",
    Effects.ReducedCastSpeed: "Reduced cast speed",
    Effects.IncreasedCriticalHitDamage: "Increased crit. damage",
    Effects.RemoveItem: "Remove item",
    Effects.AddItem: "Add item",
    Effects.Teleport: "Teleport",
}

def status_effects_text(item: Item):
    effects_asdf = []
    for thing in item.effects:
        stat_name = status_effect_names[thing.effect]
        stat_duration = ""
        if thing.duration > 0:
            stat_duration = f"for {format_seconds(thing.duration, TimeSize.MEDIUM)}"
        stat_percent = ""
        if thing.percentage > 0:
            stat_percent = f"+{('%.1f' % (thing.percentage*100)).rstrip('0').rstrip('.')}%"
        stat_min = ""
        if thing.min_amount > 0:
            stat_min = f"(Min: {thing.min_amount})"
        effects_asdf.append(strjoin(' ', stat_name, stat_percent, stat_min, stat_duration))
    return ", ".join(effects_asdf)


async def main():
    for item in ravenpy.get_all_items():
        if item.effects:
            print(f"{item.name} - {status_effects_text(item)}")
if __name__ == "__main__":
    asyncio.run(main())

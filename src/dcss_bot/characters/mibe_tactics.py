"""MiBe's observable, one-action tactical safety layer.

Damage estimates deliberately expose uncertainty. They rank short-horizon risk;
they are not a combat simulator and never use an instance's hidden HP or equipment.
"""
from dataclasses import asdict, dataclass, replace
from collections import Counter, deque
from math import ceil, isfinite
import re

from ..models import Action, DIRECTIONS, Monster, Observation
from ..item_names import category_from_name, public_item_name
from ..terrain import CLOSED_DOORS, DOWNSTAIRS, LAIR_ENTRANCE, LAIR_EXIT, SOLID_CORNER_SIDES, UPSTAIRS
from .mibe_mechanics import (BERSERK_HP_MULTIPLIER, BROTHERS_ALLIES, OPPORTUNITY_CHANCE, STAIR_ARRIVAL_MULTIPLIER,
                             STAIR_DEPARTURE_ACTIONS, confirmed_unseen_kill, mechanics, minor_summoner, public_hp_estimate,
                             short_caster_book, visible_dangerous_weapon)
from .mibe_equipment import axe_type, is_flaming_axe
from .mibe_retreat import plan_withdrawal
from .mibe_stairs import StairReevaluation
from .mibe_encounter import EncounterProgress
from .mibe_unseen import FailedUnseenDisplacements, UnseenMeleeSearch
from .mibe_geometry import WalkingDistances
from .mibe_approach import BoundedShooterApproach


@dataclass(frozen=True)
class TacticalConfig:
    horizon_actions: float = 3
    dangerous_fraction: float = .35
    emergency_fraction: float = .7
    berserk_min_hp: float = .7
    lure_step_limit: int = 8
    ineffective_retreat_limit: int = 2
    recent_enemy_memory_turns: int = 12
    return_stair_memory_turns: int = 1000
    stair_risk_fraction: float = .85
    berserk_recovery_limit: int = 80

    def __post_init__(self):
        if not isfinite(self.horizon_actions) or not 1 <= self.horizon_actions <= 8:
            raise ValueError("Tactical horizon must be 1–8 actions")
        if not 0 < self.dangerous_fraction < self.emergency_fraction <= 1:
            raise ValueError("Danger/emergency fractions must be ordered within (0, 1]")
        if not 0 < self.berserk_min_hp <= 1 or not 1 <= self.lure_step_limit <= 24:
            raise ValueError("Invalid Berserk HP threshold or lure bound")
        if not 1 <= self.ineffective_retreat_limit <= 8 or not 1 <= self.recent_enemy_memory_turns <= 30:
            raise ValueError("Invalid retreat or recent-enemy memory bound")
        if not 10 <= self.return_stair_memory_turns <= 1000 or not 0 < self.stair_risk_fraction < 1:
            raise ValueError("Invalid return-stair memory or risk budget")
        if not 1 <= self.berserk_recovery_limit <= 200:
            raise ValueError("Invalid bounded Berserk recovery interval")


@dataclass(frozen=True)
class Threat:
    name: str
    position: tuple[int, int]
    distance: int
    speed: float
    expected_loss: float
    burst_loss: float
    ranged: bool
    disabling: bool
    summoning: bool
    avoid_melee: bool
    uncertainty: str
    movement_speed: float = 1.
    walking_steps_lower_bound: int | None = None
    entangling: bool = False
    melee_prohibited: bool = False
    ally_reposition: bool = False
    ally_might: bool = False
    damage_requires_adjacency: bool = False


@dataclass(frozen=True)
class Assessment:
    category: str
    horizon: float
    expected_loss: float
    burst_loss: float
    recent_damage: int
    simultaneous_attackers: int
    escape_steps: int
    reasons: tuple[str, ...]
    threats: tuple[Threat, ...]


@dataclass(frozen=True)
class Candidate:
    action: Action
    priority: int
    expected_loss: float
    burst_loss: float
    resource: str = "none"


@dataclass(frozen=True)
class Avoidance:
    position: tuple[int, int]
    xl: int
    reason: str
    name: str


@dataclass(frozen=True)
class ReturnStair:
    position: tuple[int, int]
    destination: str
    departure_feature: int
    departure_turn: int
    observed_clear: bool


@dataclass(frozen=True)
class WithdrawalGoal:
    location: str
    position: tuple[int, int]
    kind: str
    started_turn: int
    last_distance: int


class TacticalController:
    def __init__(self, config: TacticalConfig | None = None):
        self.config = config or TacticalConfig()
        self.previous: Observation | None = None
        self._observed: Observation | None = None
        self._damage: tuple[str, int, int] | None = None
        self._walking: WalkingDistances | None = None
        self._walking_observation: Observation | None = None
        self._withdrawal_progress: int | None = None
        self._retreat_moved = False
        self.last_action: Action | None = None
        self.avoided: dict[str, list[Avoidance]] = {}
        self.unsafe_stairs: dict[tuple[str, int], int] = {}
        self.return_stairs: dict[tuple[str, int], ReturnStair] = {}
        self.stair_reevaluation = StairReevaluation()
        self.encounter_start = -1
        self.encounter_last_seen = -100
        self.lure_steps = 0
        self.ineffective_retreats = 0
        self.recent_hostiles: dict[str, tuple[int, int]] = {}
        self.latest_assessment: Assessment | None = None
        self.last_seen_hostiles: dict[int, tuple[Monster,int]] = {}
        self._assessment_observation: Observation | None = None
        self.hold_until = -1
        self.inspected_monsters: set[tuple[str, int]] = set()
        self.refuted_cover: set[tuple[str, tuple[int, int], str]] = set()
        self.unseen_harm: tuple[str, int, int] | None = None
        self.unseen_contact: tuple[Monster, int] | None = None
        self.unseen_attempted = False
        self.unseen_melee_turn: tuple[str,int] | None = None
        self.unseen_search = UnseenMeleeSearch()
        self.unseen_displacements = FailedUnseenDisplacements()
        self.encounter_progress = EncounterProgress()
        self.shooter_approach = BoundedShooterApproach()
        self.withdrawal: WithdrawalGoal | None = None
        self.failed_withdrawals: set[tuple[str, tuple[int, int]]] = set()
        # Native IDs may change after Blink/LOS loss. A public name on this
        # floor shares its allowance until an observed named kill resolves it.
        self.minor_summoner_approaches: set[tuple[str,str]] = set()
        self.minor_summoner_preparations: set[tuple[str,str]] = set()
        self.short_caster_closes: dict[tuple[str,str], tuple[int,int]] = {}
        self.minor_summon_interceptions: set[tuple[str,str]] = set()

    @staticmethod
    def status(obs):
        return " ".join((*obs.statuses, *(entry.text for entry in obs.status_details))).casefold()

    @staticmethod
    def ability(obs, name):
        status = TacticalController.status(obs)
        if (any(s.casefold() == "berserk" for s in obs.statuses)
                or any(mark in status for mark in ("confus", "silenc", "paralys", "petrified"))):
            return False
        label = {"berserk": "berserk", "trogs_hand": "trog's hand", "brothers_in_arms": "brothers in arms"}[name]
        return any(entry.name.casefold() == label and entry.available
                   and (entry.failure is None or entry.failure < 100) for entry in obs.abilities)

    @staticmethod
    def item(obs, *names, kind=None):
        for name in names:
            for item in obs.inventory:
                required = {"quaff": "potion", "read": "scroll", "zap": "wand", "throw": "missile"}.get(kind)
                label = public_item_name(item.name)
                if required and (item.category or category_from_name(label)) != required:
                    continue
                if name in label and not any(mark in label for mark in ("unknown", "unidentified")):
                    return item
        return None

    def new_messages(self, obs):
        old = self.previous
        if old is None or old.location != obs.location:
            return obs.messages
        for overlap in range(min(len(old.messages), len(obs.messages)), 0, -1):
            if old.messages[-overlap:] == obs.messages[:overlap]:
                return obs.messages[overlap:]
        return obs.messages

    def recent_damage(self, obs):
        if self._damage is not None and self._damage[:2] == (obs.location, obs.turn):
            return self._damage[2]
        old = self.previous
        if old is None or old.location != obs.location or obs.turn <= old.turn:
            return 0
        before = old.hp
        old_berserk = any(s.casefold() == "berserk" for s in old.statuses)
        new_berserk = any(s.casefold() == "berserk" for s in obs.statuses)
        if old_berserk != new_berserk and old.hp_max and old.hp_max != obs.hp_max:
            # Berserk changes the HP scale; draining does not. Do not discard
            # real incoming damage merely because draining also reduced max HP.
            before = old.hp * obs.hp_max // old.hp_max
        return max(0, before - obs.hp)

    def _unseen_loss_estimate(self, obs):
        # A just-vanished examined attacker is not a one-damage creature merely
        # because its first unseen attack missed. Keep forecast and actual HP
        # loss separate; do not renew incoming-harm timestamps from player hits.
        contact = mechanics(self.unseen_contact[0]) if self.unseen_contact is not None else None
        return max(1,self.recent_damage(obs),self.unseen_harm[2] if self.unseen_harm else 0,
                   max(contact.melee,contact.ranged_max) if contact else 0)

    def walking_geometry(self, obs: Observation) -> WalkingDistances:
        if self._walking is None or self._walking_observation is not obs:
            self._walking = WalkingDistances({(cell.x,cell.y):cell for cell in obs.cells})
            self._walking_observation = obs
        return self._walking

    def observe(self, obs):
        # decide() and routine() share immutable snapshots. Account for their
        # world transition once, before either caller replaces previous/action.
        if obs is self._observed:
            return
        old = self.previous
        self.encounter_progress.observe(obs,old,self.last_action)
        self.shooter_approach.observe(obs)
        self.stair_reevaluation.observe(
            obs,old,self.last_action,unseen_harm=self.unseen_harm is not None)
        if self._damage is None or self._damage[:2] != (obs.location, obs.turn):
            self._damage = obs.location, obs.turn, self.recent_damage(obs)
            advanced = old is not None and old.location == obs.location and obs.turn > old.turn
            self._withdrawal_progress = (
                self.withdrawal.last_distance if advanced and self.withdrawal is not None
                and self.last_action is not None and self.last_action.objective == "planned retreat"
                else None)
            self._retreat_moved = bool(
                advanced and self.last_action is not None and self.last_action.objective == "retreat"
                and obs.position != old.position)
        if (old is not None and old.place == obs.place and obs.depth == old.depth + 1
                and self.last_action is not None and self.last_action.kind == "stairs_down"):
            departure = next((cell.feature for cell in old.cells if (cell.x,cell.y) == old.position),None)
            arrival = next((cell.feature for cell in obs.cells if (cell.x,cell.y) == obs.position),None)
            if departure in DOWNSTAIRS and arrival in UPSTAIRS:
                clear = (not old.hostiles and self.unseen_harm is None
                         and not any(count for count,_ in self.recent_hostiles.values()))
                self.return_stairs[(obs.location,arrival)] = ReturnStair(
                    obs.position,old.location,departure,old.turn,clear)
        if old is not None and old.location != obs.location:
            self.recent_hostiles.clear()
            self.last_seen_hostiles.clear()
            self.unseen_harm = None
            self.unseen_contact = None
            self.unseen_melee_turn = None
        new_messages = self.new_messages(obs)
        self.unseen_displacements.observe(obs,old,self.last_action,new_messages)
        killed_names = [match[1].casefold() for message in new_messages
                        if (match := re.search(
                            r"\bYou (?:kill|slay|destroy) (?:the )?(.+?)[!.](?:$| )",message))]
        if old is not None and old.location == obs.location and obs.turn > old.turn:
            damage = self.recent_damage(obs)
            blocked_unseen = any(re.search(r"\byou block something['’]s attack\b",message.casefold())
                                 for message in new_messages)
            unseen_hit = blocked_unseen or any(re.search(
                r"\bsomething (?:(?:barely|closely|completely) )?(?:hits|misses|bites|claws|stings|touches|gazes|attacks|casts|spits|breathes|smites|shoots)\b",
                message.casefold()) for message in new_messages)
            thin_air = any("out of thin air" in message.casefold() for message in new_messages)
            mumbled = any(re.search(r"\bstrange,?\s+mumbled words\b",message.casefold())
                          for message in new_messages)
            targeted = any(re.search(r"\b(?:hits|misses)\s+you\b|\byou resist\b",message.casefold())
                           for message in new_messages)
            unseen_spell = (thin_air or mumbled) and (damage or targeted)
            if blocked_unseen or any(re.search(r"\bsomething (?:(?:barely|closely|completely) )?(?:hits|misses|bites|claws|stings|touches)\b",
                             message.casefold()) for message in new_messages):
                self.unseen_melee_turn = obs.location,obs.turn
            ongoing = (damage and self.unseen_harm is not None
                       and obs.turn <= self.unseen_harm[1]+2
                       and not any(s.casefold().startswith("pois") or s.casefold()=="fire" for s in obs.statuses)
                       and not any(cell.cloud and (cell.x,cell.y)==obs.position for cell in obs.cells))
            if unseen_hit or unseen_spell or ongoing:
                previous_loss = (self.unseen_harm[2] if self.unseen_harm is not None
                                 and obs.turn <= self.unseen_harm[1]+2 else 0)
                self.unseen_harm = obs.location, obs.turn, max(1,damage,previous_loss)
            disappeared = [enemy for enemy in old.hostiles
                           if enemy.distance(obs.position) <= 1
                           and all(enemy.id != current.id for current in obs.monsters)
                           and enemy.name.casefold() not in killed_names]
            vanished = any("invisib" in message.casefold() or "vanish" in message.casefold()
                           for message in new_messages)
            if disappeared and (unseen_hit or vanished):
                self.unseen_contact = min(disappeared, key=lambda enemy: enemy.distance(obs.position)), obs.turn
                self.unseen_attempted = False
                if vanished:
                    self.unseen_harm = obs.location, obs.turn, damage
            if (self.last_action is not None and self.last_action.value in DIRECTIONS
                    and confirmed_unseen_kill(
                        old,obs,self.last_action,
                        (old.position[0]+DIRECTIONS[self.last_action.value][0],
                         old.position[1]+DIRECTIONS[self.last_action.value][1]))):
                self.unseen_contact = None
                if not unseen_hit and not unseen_spell and not damage:
                    self.unseen_harm = None
                    self.hold_until = max(self.hold_until,obs.turn+2)
        if self.unseen_harm and obs.turn > self.unseen_harm[1] + 2:
            self.unseen_harm = None
        if self.unseen_contact and obs.turn > self.unseen_contact[1] + 2:
            self.unseen_contact = None
        if (old is not None and old.location == obs.location and self.last_action is not None
                and self.last_action.objective == "retreat" and obs.turn > old.turn
                and obs.position != old.position):
            def pressure(snapshot):
                horizon = self.config.horizon_actions * max(.5,snapshot.attack_delay or 1.)
                return (sum(mechanics(enemy).ranged and enemy.distance(snapshot.position)
                            < mechanics(enemy).ranged_range + horizon*mechanics(enemy).movement_speed
                            for enemy in snapshot.hostiles),
                        sum(enemy.distance(snapshot.position) <= max(mechanics(enemy).reach,
                            2 if self.witnessed_reach(snapshot,enemy) else 1) for enemy in snapshot.hostiles),
                        len(snapshot.hostiles))
            before_pressure, after_pressure = pressure(old), pressure(obs)
            if any(after < before for before,after in zip(before_pressure,after_pressure)):
                self.ineffective_retreats = 0
            else:
                self.ineffective_retreats += 1
        if old is not None:
            for name in killed_names:
                count,seen = self.recent_hostiles.get(name,(0,obs.turn))
                self.recent_hostiles[name] = max(0,count-1),seen
                if count <= 1 and not any(enemy.name.casefold() == name for enemy in obs.hostiles):
                    self.minor_summoner_approaches.discard((old.location,name))
                    self.minor_summoner_preparations.discard((old.location,name))
                    self.short_caster_closes.pop((old.location,name),None)
                    self.minor_summon_interceptions.discard((old.location,name))
                self.shooter_approach.cancelled.pop((old.location,name),None)
            if killed_names:
                if (self.unseen_contact and self.unseen_contact[0].name.casefold() in killed_names
                        and not self.recent_hostiles.get(self.unseen_contact[0].name.casefold(),(0,0))[0]):
                    self.unseen_contact = None
                    # A named victim is not proof that another unseen attacker
                    # stopped hitting during this same world action.
                    if self.unseen_harm is not None and self.unseen_harm[1] != obs.turn:
                        self.unseen_harm = None
                self.avoided[obs.location] = [
                    entry for entry in self.avoided.get(obs.location, ())
                    if entry.name.casefold() not in killed_names
                    or self.recent_hostiles.get(entry.name.casefold(),(0,0))[0]]
        if old is not None and old.hostiles and not obs.hostiles and old.location == obs.location:
            self.hold_until = obs.turn + 2
        if old is not None and old.location != obs.location:
            # A return stair completes this floor's withdrawal. Otherwise the
            # old goal starts a new retreat on every quiet arrival, climbing
            # the entire dungeon and banning each harmless reverse stair.
            self.withdrawal = None
            if self.last_action is not None and self.last_action.kind in {"stairs_down", "stairs_up", "escape_upstairs"}:
                feature = next((cell.feature for cell in old.cells if (cell.x,cell.y)==old.position),None)
                if obs.hostiles and feature is not None:
                    # Use the public stair variant, not coordinates that reset
                    # their origin when returning to this level.
                    self.unsafe_stairs[(old.location,feature)] = obs.xl + 2
                    self._remember(obs, "unsafe stair arrival")
                    if (old.location,feature) in self.return_stairs:
                        self.return_stairs[(old.location,feature)] = replace(
                            self.return_stairs[(old.location,feature)],observed_clear=False)
                if (self.last_action.kind == "escape_upstairs"
                        or self.last_action.kind == "stairs_up" and self.last_action.objective == "planned retreat"):
                    self._remember(old,"hostiles left behind during return-stair escape")
                    reverse = next((cell.feature for cell in obs.cells if (cell.x,cell.y) == obs.position),None)
                    if reverse in DOWNSTAIRS:
                        self.unsafe_stairs[(obs.location,reverse)] = obs.xl + 2
            self.ineffective_retreats = 0
            self.lure_steps = 0
            self.encounter_start = -1
        if obs.hostiles:
            if self.encounter_start < 0 or obs.turn - self.encounter_last_seen > 8:
                self.encounter_start = obs.turn
                self.lure_steps = 0
                self.ineffective_retreats = 0
                self.failed_withdrawals.clear()
            self.encounter_last_seen = obs.turn
        elif obs.turn - self.encounter_last_seen > 8:
            self.encounter_start = -1
            self.lure_steps = 0
        for name,(count,seen) in tuple(self.recent_hostiles.items()):
            if not count or obs.turn-seen > self.config.recent_enemy_memory_turns:
                self.recent_hostiles.pop(name)
        for name,count in Counter(enemy.name.casefold() for enemy in obs.hostiles).items():
            if count >= self.recent_hostiles.get(name,(0,obs.turn))[0]:
                self.recent_hostiles[name] = count,obs.turn
        self.last_seen_hostiles = {
            identity:(enemy,seen) for identity,(enemy,seen) in self.last_seen_hostiles.items()
            if obs.turn-seen <= self.config.recent_enemy_memory_turns
            and enemy.name.casefold() in self.recent_hostiles}
        for enemy in obs.hostiles:
            self.last_seen_hostiles[enemy.id] = enemy,obs.turn
        self.unseen_search.observe(
            obs,old,self.last_action,
            fresh_melee=self.unseen_melee_turn == (obs.location,obs.turn))
        self._observed = obs

    def _remember(self, obs, reason):
        names = {enemy.name for enemy in obs.hostiles}
        memory = [entry for entry in self.avoided.get(obs.location, ()) if entry.name not in names]
        memory.extend(Avoidance((enemy.x, enemy.y), obs.xl, reason, enemy.name) for enemy in obs.hostiles)
        self.avoided[obs.location] = memory

    def unseen_displacement_evidence(self, obs):
        evidence = self.unseen_displacements.evidence(obs)
        if self.unseen_contact is not None:
            enemy, turn = self.unseen_contact
            if turn <= obs.turn <= turn + 2 and (enemy.x,enemy.y) != obs.position:
                evidence.append({
                    "square": (enemy.x,enemy.y), "turn": turn, "age": obs.turn-turn,
                    "source": "last public contact square; current occupancy unknown"})
        return evidence

    def avoided_positions(self, obs, cells):
        result = {entry["square"] for entry in self.unseen_displacement_evidence(obs)}
        for entry in self.avoided.get(obs.location, ()):
            if obs.xl < entry.xl + 3:
                result.update(position for position in cells
                              if max(abs(position[0] - entry.position[0]), abs(position[1] - entry.position[1])) <= 2)
        return result

    def _observed_mechanics(self, obs, enemy):
        info = mechanics(enemy)
        return replace(info,reach=2) if self.witnessed_reach(obs,enemy) else info

    def _corner_step(self, obs, cells, visits, covered, assessment):
        """Find short apparent cover before contact, including summoning lanes."""
        queue = deque([(obs.position, None, 0)])
        seen = {obs.position}
        occupied = {(monster.x, monster.y) for monster in obs.monsters}
        blocked = {entry["square"] for entry in self.unseen_displacement_evidence(obs)}
        def pressure(value, position):
            return sum(threat.ranged or (
                threat.summoning and not assessment.simultaneous_attackers
                and (not covered(cells,enemy,position)
                     or (obs.location,position,enemy.name) in self.refuted_cover))
                for enemy,threat in zip(obs.hostiles,value.threats))
        current_pressure = pressure(assessment,obs.position)
        if not current_pressure:
            return None
        status = self.status(obs)
        move_time = (1.5 if "slow" in status else 1.) / (
            1.5 if any(s.casefold() in {"haste","berserk"} for s in obs.statuses) else 1.)
        movement_enemies = [(enemy,self._observed_mechanics(obs,enemy)) for enemy in obs.hostiles]
        walking = self.walking_geometry(obs)
        if self.ineffective_retreats >= self.config.ineffective_retreat_limit:
            # A proved nearby cover/range endpoint is still useful before melee
            # contact. Never reopen the adjacent pursuit/AoO backpedalling loop.
            if assessment.simultaneous_attackers or self.lure_steps >= self.config.lure_step_limit:
                return None
        while queue:
            position, first, depth = queue.popleft()
            projected = self.assess(obs, cells, covered, position, duration=1.)
            if first and pressure(projected,position) < current_pressure:
                return first
            if depth >= 3:
                continue
            for key, (dx, dy) in DIRECTIONS.items():
                target = position[0] + dx, position[1] + dy
                cell = cells.get(target)
                if (target in seen or target in occupied or target in blocked
                        or not cell or not cell.visible or not cell.walkable
                        or cell.cloud or cell.feature in CLOSED_DOORS):
                    continue
                if any(walking.distance(enemy,info,target,3) < min(3,enemy.distance(obs.position))
                       for enemy,info in movement_enemies):
                    continue
                if any(enemy.distance(obs.position) > max(info.reach,2 if self.witnessed_reach(obs,enemy) else 1)
                       and walking.distance(enemy,info,target,info.reach+ceil(
                           (depth+1)*move_time*info.movement_speed-1e-9)) <= info.reach+ceil(
                           (depth+1)*move_time*info.movement_speed-1e-9)
                       for enemy,info in movement_enemies):
                    continue
                seen.add(target)
                queue.append((target, first or key, depth + 1))
        return None

    def safe_stairs(self, obs, positions):
        features = {(cell.x,cell.y):cell.feature for cell in obs.cells}
        return {position for position in positions
                if features.get(position) in UPSTAIRS | DOWNSTAIRS | {LAIR_ENTRANCE, LAIR_EXIT}
                and obs.xl >= self.unsafe_stairs.get((obs.location,features[position]),0)}

    def _withdrawal_plan(self, obs, cells, visits, covered, *, unseen_loss=0):
        goal = self.withdrawal
        if goal and (goal.location != obs.location or obs.turn-goal.started_turn > 360):
            self.withdrawal = goal = None
        if goal and obs.position == goal.position:
            if goal.kind == "upstairs":
                return None
            # A corner is an intermediate escape waypoint, not permission to
            # reopen the same encounter by returning to ordinary exploration.
            self.failed_withdrawals.add((obs.location,goal.position))
            goal = None
        blocked = {entry["square"] for entry in self.unseen_displacement_evidence(obs)}
        route_cells = ({position:cell for position,cell in cells.items() if position not in blocked}
                       if blocked else cells)
        plan = None
        horizon = ceil(self.config.horizon_actions)
        for steps in ((horizon,1) if horizon > 1 else (1,)):
            candidate = plan_withdrawal(obs,route_cells,visits,covered,
                                        target=goal.position if goal else None,max_steps=steps,
                                        walking=self.walking_geometry(obs))
            if candidate is None:
                continue
            if unseen_loss:
                expected = candidate.expected_loss + unseen_loss*candidate.horizon
                burst = candidate.burst_loss + max(4,unseen_loss*2)*ceil(candidate.horizon)
                if expected >= obs.hp*.35 or burst >= obs.hp*.8:
                    continue
                candidate = replace(candidate,expected_loss=round(expected,2),burst_loss=round(burst,2),
                    uncertainty=(*candidate.uncertainty,
                    "unseen attack/AoO risk extrapolated from recent loss with an uncertainty margin, not a damage bound or known position"))
            plan = candidate
            break
        if plan is None:
            return None
        if (obs.location,plan.goal) in self.failed_withdrawals:
            return None
        if sum(count for count,_ in self.recent_hostiles.values()) > len(obs.hostiles):
            plan = replace(plan,uncertainty=(*plan.uncertainty,
                "recent unseen hostiles have unknown positions; the visible-enemy forecast is not proof of no incoming attacks"))
        distance = self._withdrawal_progress
        self._withdrawal_progress = None
        if goal and distance is not None and len(plan.path) >= distance:
            self.failed_withdrawals.add((obs.location,plan.goal))
            self.withdrawal = None
            return None
        self.withdrawal = WithdrawalGoal(obs.location,plan.goal,plan.goal_kind,
                                         goal.started_turn if goal else obs.turn,len(plan.path))
        self._remember(obs,"hostiles left behind during planned withdrawal")
        return plan

    def _unresolved_can_reach_stair(self, obs, departure_time):
        """Refuse unknown positions unless public last-seen bounds exclude contact.

        Stale/duplicate native IDs can only make this more conservative. Never
        match spellbooks to a new instance by name or assume disappearance killed it.
        """
        visible = Counter(enemy.name.casefold() for enemy in obs.hostiles)
        visible_ids = {enemy.id for enemy in obs.hostiles}
        for name,(count,_) in self.recent_hostiles.items():
            missing = count-visible[name]
            if missing <= 0:
                continue
            records = [(enemy,seen) for identity,(enemy,seen) in self.last_seen_hostiles.items()
                       if identity not in visible_ids and enemy.name.casefold() == name]
            if len(records) < missing:
                return True
            for enemy,seen in records:
                info = mechanics(enemy)
                if (enemy.description is None or info.ranged or info.summons or info.disables
                        or enemy.spells or visible_dangerous_weapon(enemy)):
                    return True
                travel = ceil((max(0,obs.turn-seen)+departure_time)*info.movement_speed)
                if enemy.distance(obs.position) <= max(info.reach,2 if self.witnessed_reach(obs,enemy) else 1)+travel+1:
                    return True
        return False

    def _return_stair_escape(self, obs, cells, covered, assessment, move_time):
        feature = cells.get(obs.position)
        link = self.return_stairs.get((obs.location,feature.feature if feature else None))
        status = self.status(obs)
        departure_time = ceil(STAIR_DEPARTURE_ACTIONS*move_time*10)/10
        if (link is None or link.position != obs.position or not link.observed_clear
                or obs.turn-link.departure_turn > self.config.return_stair_memory_turns
                or not obs.hostiles or any(enemy.distance(obs.position) <= 1 for enemy in obs.hostiles)
                or any(enemy.description is None for enemy in obs.hostiles)
                or self.unseen_harm is not None or self.unseen_contact is not None
                or self._unresolved_can_reach_stair(obs,departure_time)
                or any(mark in status for mark in ("tele","conf","petr","paral","held","constr","net"))
                or feature.cloud):
            return None
        departure = self.assess(
            obs,cells,covered,duration=departure_time/self.config.horizon_actions/
            (1.5 if any(s.casefold()=="slow" for s in obs.statuses) else 1.))
        arrival_time = ceil(departure_time*STAIR_ARRIVAL_MULTIPLIER*10)/10
        # Previously clear is not guaranteed clear. Reserve an explicit policy
        # allowance for arrival uncertainty, rather than assuming zero damage.
        age_allowance = .1 + min(.25,max(0,obs.turn-link.departure_turn-100)/2000)
        allowance = max(3.,obs.hp_max*age_allowance)*arrival_time/STAIR_ARRIVAL_MULTIPLIER
        expected = departure.expected_loss+allowance
        possible_hits = []
        for enemy in obs.hostiles:
            info = mechanics(enemy)
            reach = max(info.reach,2 if self.witnessed_reach(obs,enemy) else 1)
            distance = enemy.distance(obs.position)
            if departure_time > max(0.,distance-reach)/info.movement_speed:
                possible_hits.append(info.melee)
            if info.ranged and departure_time > max(0.,distance-info.ranged_range)/info.movement_speed:
                possible_hits.append(info.ranged_max)
        largest_hit = max(possible_hits,default=0.)
        if (expected >= obs.hp*self.config.stair_risk_fraction
                or departure.burst_loss >= obs.hp*.95
                or largest_hit >= obs.hp*.9
                or expected >= assessment.expected_loss and not any(t.summoning for t in assessment.threats)):
            return None
        risk = replace(departure,expected_loss=round(expected,2),
                       burst_loss=round(departure.burst_loss+allowance,2))
        evidence = {**asdict(link),"departure_actions":departure_time,"arrival_actions":arrival_time,
                    "arrival_uncertainty_fraction":age_allowance,
                    "arrival_uncertainty_allowance":allowance,"expected_loss":expected,
                    "departure_burst":departure.burst_loss,"visible_adjacent_followers":0,
                    "largest_possible_departure_hit":largest_hit,
                    "destination_certainty":"previously clear, not guaranteed safe"}
        return risk,evidence

    def rebase(self, location: str, offset: tuple[int,int] | None):
        """Rebase position evidence only when public map anchors prove it."""
        self.inspected_monsters = {identity for identity in self.inspected_monsters if identity[0] != location}
        self.unseen_search.rebase(location,offset)
        self.unseen_displacements.rebase(location,offset)
        self.shooter_approach.rebase(location,offset)
        self._walking_observation = None
        if self._assessment_observation is not None and self._assessment_observation.location == location:
            self._assessment_observation = None
        if offset is None:
            if self._observed is not None and self._observed.location == location:
                self._observed = None
            if self._damage is not None and self._damage[0] == location:
                self._damage = None
                self._withdrawal_progress = None
                self._retreat_moved = False
            self.avoided.pop(location,None)
            self.return_stairs = {key:entry for key,entry in self.return_stairs.items() if key[0] != location}
            if self.withdrawal and self.withdrawal.location == location:
                self.withdrawal = None
            self.failed_withdrawals = {entry for entry in self.failed_withdrawals if entry[0] != location}
            self.refuted_cover = {entry for entry in self.refuted_cover if entry[0] != location}
            if self.previous is not None and self.previous.location == location:
                self.unseen_harm = None
                self.unseen_contact = None
                self.previous = None
                self.last_action = None
                self.recent_hostiles.clear()
                self.last_seen_hostiles.clear()
                self.unseen_melee_turn = None
                self.ineffective_retreats = 0
            return
        dx,dy = offset
        if not dx and not dy:
            return
        def shifted(position):
            return position[0]+dx,position[1]+dy
        self.return_stairs = {
            key:replace(entry,position=shifted(entry.position)) if key[0] == location else entry
            for key,entry in self.return_stairs.items()}
        if self.withdrawal and self.withdrawal.location == location:
            self.withdrawal = replace(self.withdrawal,position=shifted(self.withdrawal.position))
        self.failed_withdrawals = {(place,shifted(position) if place == location else position)
                                   for place,position in self.failed_withdrawals}
        if self.unseen_contact is not None and self.previous is not None and self.previous.location == location:
            enemy, seen = self.unseen_contact
            self.unseen_contact = replace(enemy, x=enemy.x+dx, y=enemy.y+dy), seen
        if location in self.avoided:
            self.avoided[location] = [replace(entry,position=shifted(entry.position)) for entry in self.avoided[location]]
        self.refuted_cover = {(place,shifted(position) if place == location else position,name)
                              for place,position,name in self.refuted_cover}
        old = self.previous
        if old is not None and old.location == location:
            self.previous = replace(old,position=shifted(old.position),
                                    cells=tuple(replace(cell,x=cell.x+dx,y=cell.y+dy) for cell in old.cells),
                                    monsters=tuple(replace(enemy,x=enemy.x+dx,y=enemy.y+dy) for enemy in old.monsters))
            self.last_seen_hostiles = {
                identity:(replace(enemy,x=enemy.x+dx,y=enemy.y+dy),seen)
                for identity,(enemy,seen) in self.last_seen_hostiles.items()}

    @staticmethod
    def witnessed_reach(obs, enemy):
        return any(enemy.name.casefold() in message.casefold() and "you from afar" in message.casefold()
                   for message in obs.messages[-5:])

    def _small_book_fallback(self, obs, cells, covered, steps, move_duration):
        """Finite public-book actions after resources and useful routes failed.

        Slow is not Berserk cooldown. Nevertheless every exposure here reserves
        its 50% action-time penalty, including a possible newly landed Slow.
        These are risk budgets, not Will immunity or complete-fight guarantees.
        """
        status = self.status(obs)
        if (obs.hp_ratio < .8 or not obs.hostiles or len(obs.hostiles) > 2
                or sum(count for count,_ in self.recent_hostiles.values()) > len(obs.hostiles)
                or any(mark in status for mark in
                       ("berserk","pois","exh","conf","petr","paral","tele","held","net","web","fire"))
                or self.unseen_harm is not None or self.unseen_contact is not None
                or self.unseen_displacements.active(obs)):
            return None
        examined = [(enemy,self._observed_mechanics(obs,enemy)) for enemy in obs.hostiles]
        if any(enemy.description is None or info.poison or info.escalates or info.avoid_melee
               or info.melee_prohibited or visible_dangerous_weapon(enemy) for enemy,info in examined):
            return None
        books = [enemy for enemy,info in examined if enemy.spells]
        if len(books) != 1:
            return None
        target = books[0]
        key = obs.location,target.name.casefold()
        info = self._observed_mechanics(obs,target)
        companions = [(enemy,body) for enemy,body in examined if enemy is not target]
        short = short_caster_book(target) and 0 < (public_hp_estimate(target) or 0) <= 25
        minor = minor_summoner(target) and 0 < (public_hp_estimate(target) or 0) <= 40
        if (not (short or minor) or info.melee > 12 or info.movement_speed >= 1.5
                or info.ranged_max > 15
                or any(body.ranged or body.disables or body.summons or body.melee > 3
                       or not 0 < (public_hp_estimate(enemy) or 0) <= 5
                       for enemy,body in companions)):
            return None
        # A full maximum body hit (two for the blinking summoner) remains in
        # the expected-loss reserve after this action. Burst must separately
        # remain nonlethal; the expected aftermath reserve is not guaranteed.
        reserve = max(obs.hp_max*.4,info.melee*(1 if short else 2))
        duration = max(move_duration,1.)/self.config.horizon_actions
        if short:
            duration = 1.5/self.config.horizon_actions/(1.5 if "slow" in status else 1.)
        def exposure(position=None):
            risk = self.assess(obs,cells,covered,position,duration=duration)
            if short:
                # Dart cannot miss. Do not buy this close with the general
                # EV discount: retain at least the displayed raw projectile
                # mean over the slowed action, without inventing spell power.
                ranged = next(threat for threat in risk.threats
                              if threat.position == (target.x,target.y))
                raw = info.ranged_mean*risk.horizon*info.speed if ranged.ranged else 0
                risk = replace(risk,expected_loss=round(risk.expected_loss
                               + max(0,raw-ranged.expected_loss),2))
            return risk
        standing = exposure()
        def affordable(risk):
            return (risk.expected_loss < obs.hp*(.5 if short else .35)
                    and risk.expected_loss+reserve < obs.hp
                    and risk.burst_loss < obs.hp*.95)
        evidence = {"public_identity": list(key), "aftermath_reserve": reserve,
                    "action_world_time": standing.horizon,
                    "uncertainty": "Expected aftermath reserve, not guaranteed survival; new summons and Slow require fresh observation"}
        if minor:
            # Do not refill the summoner's spent approach or chase Blink.
            # One Hand attempt can instead prepare for one weak visible summon.
            if (not companions or target.distance(obs.position) < 2 or "slow" in status
                    or not affordable(standing)):
                return None
            active = any("hand" in s.casefold() or "regen" in s.casefold() for s in obs.statuses)
            if (obs.hp < obs.hp_max and not active and self.ability(obs,"trogs_hand")
                    and key not in self.minor_summoner_preparations):
                action = Action("ability","trogs_hand",
                                "Prepare regeneration once for an examined minor-only summoner and one weak summon; no Blink pursuit or repeated failed preparation",
                                "prepare minor summoner")
                return Candidate(action,39,standing.expected_loss,standing.burst_loss,"piety"),key,evidence
            if key in self.minor_summon_interceptions:
                return None
        else:
            distance = target.distance(obs.position)
            if not 2 <= distance <= 5:
                return None
            remaining,previous_distance = self.short_caster_closes.get(key,(distance-1,distance+1))
            if remaining <= 0 or distance >= previous_distance:
                return None
            evidence["remaining_closes"] = remaining
        closing = []
        for _,_,_,direction,_,_ in steps:
            dx,dy = DIRECTIONS[direction]
            position = obs.position[0]+dx,obs.position[1]+dy
            if short:
                progress = target.distance(position) < target.distance(obs.position)
            else:
                progress = (target.distance(position) >= 2
                            and any(enemy.distance(position) == 1
                                    and enemy.distance(obs.position) > 1 for enemy,_ in companions))
            if not progress:
                continue
            risk = exposure(position)
            if risk.simultaneous_attackers <= 1 and affordable(risk):
                closing.append((risk.expected_loss,risk.burst_loss,direction,risk))
        if not closing:
            return None
        _,_,direction,risk = min(closing)
        action = Action("move",direction,
                        ("Close once on the displayed low-HP Dart/Slow/Call Imp book; budget a slowed action and a maximum body hit in the expected aftermath, without assuming Will immunity"
                         if short else
                         "Intercept the one examined weak summon once while staying outside the summoner's melee; no renewed summoner approach or Blink chase"),
                        "bounded caster close" if short else "intercept minor summon")
        return Candidate(action,79,risk.expected_loss,risk.burst_loss),key,evidence

    def assess(self, obs, cells, covered, position=None, duration=None):
        position = obs.position if position is None else position
        status = self.status(obs)
        duration = max(.5, obs.attack_delay or 1.) if duration is None else duration
        horizon = self.config.horizon_actions * duration * (1.5 if "slow" in status else 1.)
        ac = obs.ac or 0
        ev = obs.ev if obs.ev is not None else 8
        sh = obs.sh or 0
        avoidance = min(.70, max(.25, .72 - ev * .018 - sh * .008))
        threats = []
        reasons = []
        adjacent = 0
        hydra_safe = any(item.equipped and is_flaming_axe(public_item_name(item.name)) for item in obs.inventory)
        walking = self.walking_geometry(obs)
        for enemy in obs.hostiles:
            info = self._observed_mechanics(obs,enemy)
            distance = enemy.distance(position)
            adjacent += distance <= info.reach
            cover_hint = (covered(cells, enemy, position)
                          and (obs.location, position, enemy.name) not in self.refuted_cover)
            firing_lane = (info.ranged and distance >= info.ranged_min_range
                           and (position == obs.position or not cover_hint))
            walking_distance = walking.distance(
                enemy,info,position,info.reach+ceil(horizon*info.movement_speed))
            melee_exposed = max(0.,horizon-max(0.,walking_distance-info.reach)/info.movement_speed)
            ranged_exposed = (max(0., horizon-max(0., distance-info.ranged_range)/info.movement_speed)
                              if firing_lane else 0.)
            ranged = ranged_exposed > 0
            exposed = max(melee_exposed, ranged_exposed)
            # Travel leaves less time for melee. A weak projectile does not
            # acquire the damage of an alternative weapon attack at range.
            melee_hits, ranged_hits = melee_exposed*info.speed, ranged_exposed*info.speed
            expected = max(max(1.,info.melee_mean-ac/3)*avoidance*melee_hits,
                           (max(1.,info.ranged_mean-ac/3) if info.ranged_mean > 0 else 0.)*avoidance*ranged_hits)
            burst = max(info.melee*ceil(melee_hits),info.ranged_max*ceil(ranged_hits))
            if info.poison and not any(r.name.casefold() in {"rpois", "poison"} and r.level > 0 for r in obs.resistances):
                expected += 2 * exposed
            disabling = info.disables and (not info.will_check or obs.will is None or obs.will < 3)
            if disabling and exposed:
                expected *= 1.35
            if info.summons and exposed:
                expected *= 1.3
            burst = max(burst,expected)
            hydra_exempt = "hydra" in enemy.name.casefold() and hydra_safe
            weapon_forbidden = visible_dangerous_weapon(enemy)
            prohibited = info.melee_prohibited and not hydra_exempt or weapon_forbidden
            avoid = info.avoid_melee and not hydra_exempt or weapon_forbidden
            if "slime creature" in enemy.name.casefold() and sum("slime creature" in m.name.casefold() for m in obs.hostiles) > 1:
                avoid = prohibited = True
            threats.append(Threat(enemy.name, (enemy.x, enemy.y), distance, info.speed, round(expected, 2),
                                  round(burst, 2), ranged, disabling, info.summons, avoid, info.uncertainty,
                                  movement_speed=info.movement_speed,walking_steps_lower_bound=walking_distance,
                                  entangling=info.entangles and ranged_exposed > 0,
                                  melee_prohibited=prohibited,
                                  ally_reposition=info.ally_reposition,ally_might=info.ally_might,
                                  damage_requires_adjacency=info.damage_requires_adjacency))
        escape = sum(1 for dx, dy in DIRECTIONS.values()
                     if (cell := cells.get((position[0] + dx, position[1] + dy))) is not None
                     and cell.visible and cell.walkable and not cell.cloud and cell.feature not in CLOSED_DOORS
                     and all((monster.x,monster.y)!=(cell.x,cell.y) for monster in obs.monsters)
                     and all(monster.distance((cell.x,cell.y)) > max(mechanics(monster).reach,
                             2 if self.witnessed_reach(obs,monster) else 1) for monster in obs.hostiles))
        damage = self.recent_damage(obs)
        unseen_attack = ((self.unseen_harm is not None
                          and self.unseen_harm[0] == obs.location
                          and obs.turn <= self.unseen_harm[1] + 2)
                         or self.unseen_contact is not None and obs.turn <= self.unseen_contact[1]+2
                         or self.unseen_search.active(obs))
        uncertain_contact = self.unseen_displacements.active(obs)
        expected, burst = sum(t.expected_loss for t in threats), sum(t.burst_loss for t in threats)
        if unseen_attack:
            observed_loss = self._unseen_loss_estimate(obs)
            expected += observed_loss * horizon
            burst += observed_loss * ceil(horizon)
            reasons.append("unseen incoming attack/contact; forecast inferred from recent public combat evidence, not current visibility")
        if uncertain_contact:
            reasons.append("uncertain unseen contact: failed normal displacement; no incoming damage or monster position inferred")
        poison_emergency = ("pois" in status and obs.poison_survival is not None
                            and obs.poison_survival <= max(5, obs.hp_max * .2))
        if damage >= max(4, obs.hp_max * .18):
            reasons.append("significant recent damage")
        if any(t.avoid_melee for t in threats):
            reasons.append("high-risk melee preference or unacceptable melee mechanic/escalating encounter")
        if adjacent >= 2:
            reasons.append("multiple simultaneous attackers")
        if any(t.disabling for t in threats):
            reasons.append("disabling threat without a reliable preventive defense")
        if any(t.entangling for t in threats):
            reasons.append("web restraint can delay movement; Will does not prevent it")
        if any(t.summoning for t in threats):
            reasons.append("summoning can increase encounter size")
        support = len(threats)>1 and any(t.ally_reposition or t.ally_might for t in threats)
        if support:
            reasons.append("visible ally support can change contact formation or empower attacks; no target or active buff inferred")
        if poison_emergency:
            reasons.append("native poison survival estimate is critical")
        severe_status = any(mark in status for mark in ("conf", "petr", "paral", "corrod"))
        if severe_status:
            reasons.append("dangerous status restricts responses")
        emergency = poison_emergency or ((bool(threats) or unseen_attack) and (
            expected >= obs.hp * self.config.emergency_fraction or obs.hp <= max(8, obs.hp_max * .3) or
            (damage >= obs.hp * .4 and damage > 0)))
        avoid = any(t.avoid_melee for t in threats)
        dangerous = unseen_attack or uncertain_contact or severe_status or (bool(threats) and (
            expected >= obs.hp * self.config.dangerous_fraction or burst >= obs.hp * 1.2
            or any(enemy.threat >= 2 for enemy in obs.hostiles) or adjacent >= 2
            or severe_status or support or any(t.disabling or t.summoning for t in threats)
            or any(t.entangling for t in threats) and (len(threats)>1 or obs.hp_ratio<.7)))
        category = "emergency" if emergency else "avoid" if avoid else "dangerous" if dangerous else "manageable" if threats else "safe"
        if (len(obs.hostiles) == 1 and obs.hostiles[0].threat == 0 and obs.hp_ratio >= .8
                and not emergency and not avoid and not severe_status and not unseen_attack and not uncertain_contact
                and not any((mechanics(enemy).poison or mechanics(enemy).escalates
                             or mechanics(enemy).disables or mechanics(enemy).summons) for enemy in obs.hostiles)
                and expected < obs.hp*self.config.dangerous_fraction and burst < obs.hp*1.2
                and damage < max(4, obs.hp_max * .15)):
            category = "trivial"
        return Assessment(category, round(horizon, 2), round(expected, 2), round(burst, 2), damage,
                          adjacent, escape, tuple(reasons), tuple(threats))

    def decide(self, obs, cells, visits, covered):
        self.observe(obs)
        for enemy in obs.hostiles:
            info = mechanics(enemy)
            if (info.ranged or info.summons) and covered(cells,enemy,obs.position):
                # Actual visibility overrules the imperfect corner heuristic.
                self.refuted_cover.add((obs.location, obs.position, enemy.name))
        assessment = self.assess(obs, cells, covered)
        self.latest_assessment = assessment
        self._assessment_observation = obs
        status = self.status(obs)
        berserk = any(s.casefold() == "berserk" for s in obs.statuses)
        tele = any("tele" in s.casefold() and not s.startswith("-") for s in obs.statuses)
        # A preference to avoid a caster before committing is not an intrinsic
        # attack/cleave prohibition after rage removed the resource options.
        # Require a read public body/book; control, poison, escalation and
        # harmful melee mechanics retain their overrides even at high Will.
        committed_casters = set()
        if berserk:
            for enemy in obs.hostiles:
                info = self._observed_mechanics(obs,enemy)
                if (enemy.description is not None and enemy.spells and info.ranged
                        and re.search(r"(?im)^\s*attacks?\s+max\s*damage\b",enemy.description)
                        and not (info.melee_prohibited or info.poison or info.escalates
                                 or info.disables or info.summons or info.entangles)
                        and not visible_dangerous_weapon(enemy)):
                    committed_casters.add((enemy.x,enemy.y))

        def refuses_melee(threat):
            return (threat.melee_prohibited or threat.avoid_melee
                    and threat.position not in committed_casters)

        confused = "conf" in status
        held = bool(re.search(r"\b(?:held|netted|webbed)\b", status))
        no_scroll = confused or "silenc" in status or "-scroll" in status
        candidates = []
        rejected = []
        emergency = assessment.category == "emergency"
        dangerous = assessment.category in {"emergency", "avoid", "dangerous"}
        must_disengage = False
        stair_evidence = None
        withdrawal_evidence = None
        stair_preparation_evidence = None
        enemy_positions = {(m.x, m.y) for m in obs.monsters}
        displacement_evidence = self.unseen_displacement_evidence(obs)
        blocked_displacements = {entry["square"] for entry in displacement_evidence}
        for entry in displacement_evidence:
            rejected.append(
                f"Displacement into {entry['square']} refused: {entry['source']} "
                f"(age {entry['age']} world turns); not a known escape or inferred monster position")
        retreat_exhausted = self.ineffective_retreats >= self.config.ineffective_retreat_limit
        if retreat_exhausted:
            rejected.append("Observed pursuit/LOS defeated repeated walking retreats; prefer defence, escape or a bounded fight")
        unseen_attack = any(reason.startswith(("unseen incoming attack","uncertain unseen contact"))
                            for reason in assessment.reasons)
        support = [m for m in obs.monsters if m.attitude == 4 and not m.passive
                   and m.distance(obs.position) <= 3
                   and (m.name.casefold() in BROTHERS_ALLIES or mechanics(m).melee >= 15)]

        def add(kind, value, reason, objective, priority, loss=None, resource="none"):
            if held and kind in {"move", "stairs_up", "stairs_down", "escape_upstairs"} and objective != "escape restraint":
                # Native movement/stairs struggle before doing the named action.
                # Do not label an immobile net-cutting turn attack or retreat.
                return
            if kind == "move" and value in DIRECTIONS and objective != "escape restraint":
                dx, dy = DIRECTIONS[value]
                target = obs.position[0]+dx, obs.position[1]+dy
                if target in blocked_displacements and target not in enemy_positions:
                    return
            risk = assessment if loss is None else loss
            candidates.append(Candidate(Action(kind, value, reason, objective), priority,
                                        risk.expected_loss, risk.burst_loss, resource))

        def consume(kind, names, reason, priority, resource="consumable"):
            if kind == "quaff" and "-potion" in status:
                return False
            item = self.item(obs, *names, kind=kind)
            if item:
                add(kind, item.letter, reason, "emergency recovery" if priority < 20 else "prepare", priority, resource=resource)
                return True
            return False

        steps = []
        def front_capacity(position):
            # Unknown terrain and water may admit enemies; only known solid
            # terrain proves a restriction on the pursuers' attack frontage.
            if not obs.hostiles:
                return 0
            front = min(len(obs.hostiles),sum(
                (cells.get((position[0]+dx,position[1]+dy)) is None
                 or cells[(position[0]+dx,position[1]+dy)].feature not in SOLID_CORNER_SIDES)
                and any(enemy.distance((position[0]+dx,position[1]+dy)) < enemy.distance(position)
                        for enemy in obs.hostiles) for dx,dy in DIRECTIONS.values()))
            return max(front,sum(enemy.distance(position) <= max(mechanics(enemy).reach,
                                 2 if self.witnessed_reach(obs,enemy) else 1) for enemy in obs.hostiles))
        current_front = front_capacity(obs.position)
        current_contacts = assessment.simultaneous_attackers
        move_time = 1.5 if any(s.casefold()=="slow" for s in obs.statuses) else 1.
        if any(s.casefold() in {"berserk","haste"} for s in obs.statuses):
            move_time *= 2/3
        fast_follow = any(enemy.distance(obs.position) <= max(mechanics(enemy).reach,
                          2 if self.witnessed_reach(obs,enemy) else 1)
                          and mechanics(enemy).movement_speed*move_time >= 1 for enemy in obs.hostiles)
        faster_pursuit = any(enemy.distance(obs.position) <= max(mechanics(enemy).reach,
                             2 if self.witnessed_reach(obs,enemy) else 1)
                             and mechanics(enemy).movement_speed*move_time > 1 for enemy in obs.hostiles)
        opportunity = sum(mechanics(enemy).melee*OPPORTUNITY_CHANCE for enemy in obs.hostiles
                          if enemy.distance(obs.position) <= max(mechanics(enemy).reach,
                             2 if self.witnessed_reach(obs,enemy) else 1))
        move_duration = move_time / (1.5 if "slow" in status else 1.)
        standing_move = self.assess(obs, cells, covered, duration=move_duration)
        movement_enemies = [(enemy,self._observed_mechanics(obs,enemy)) for enemy in obs.hostiles]
        walking = self.walking_geometry(obs)
        def reaction_fronts(position):
            # Arrival can consume all movement energy and still leave a new
            # adjacent attacker for our next decision. Damage exposure alone
            # does not reserve this positional commitment.
            count = 0
            for enemy,info in movement_enemies:
                limit = info.reach+ceil(move_time*info.movement_speed-1e-9)
                count += walking.distance(enemy,info,position,limit) <= limit
            return count
        for key, (dx, dy) in DIRECTIONS.items():
            position = obs.position[0] + dx, obs.position[1] + dy
            cell = cells.get(position)
            if (not cell or not cell.visible or not cell.walkable or cell.cloud
                    or cell.feature in CLOSED_DOORS or position in enemy_positions
                    or position in blocked_displacements):
                continue
            after = self.assess(obs, cells, covered, position, duration=move_duration)
            after_front = front_capacity(position)
            # Repositioning pays possible pursuit attacks. Distance alone cannot
            # make walking away from a fast adjacent enemy an escape.
            contacts = after.simultaneous_attackers
            gain = ((after_front < current_front if fast_follow
                     else contacts < current_contacts)
                    or (current_contacts and not any(enemy.distance(obs.position) <= 1 for enemy in obs.hostiles)
                        and contacts < current_contacts and after.expected_loss < standing_move.expected_loss)
                    or sum(t.ranged for t in after.threats) < sum(t.ranged for t in assessment.threats))
            # A smaller forecast caused only by stretching static range is not
            # cover while the shooter can still close and fire in that horizon.
            ranged_relief = (not any(t.ranged for t in assessment.threats)
                             or sum(t.ranged for t in after.threats)
                             < sum(t.ranged for t in assessment.threats))
            separated = not fast_follow and ranged_relief and (
                after.expected_loss < standing_move.expected_loss - 1 or (
                    any(t.avoid_melee for t in assessment.threats)
                    and min((m.distance(position) for m in obs.hostiles), default=0)
                    > min((m.distance(obs.position) for m in obs.hostiles), default=0)))
            known = visits[position] > 0
            score = after.expected_loss + opportunity + (0 if known else 5)
            intercepted = any(enemy.distance(obs.position) > max(info.reach,2 if self.witnessed_reach(obs,enemy) else 1)
                              and walking.distance(enemy,info,position,info.reach+ceil(
                                  move_time*info.movement_speed-1e-9)) <= info.reach+ceil(
                                  move_time*info.movement_speed-1e-9)
                              for enemy,info in movement_enemies)
            steps.append((score, -int(gain), -visits[position], key, after,
                          (gain or separated) and not intercepted))
        steps.sort(key=lambda row: row[:4])
        short_casters = [enemy for enemy in obs.hostiles
                         if short_caster_book(enemy) and 0 < (public_hp_estimate(enemy) or 0) <= 25]
        caster = short_casters[0] if len(short_casters) == 1 else None
        bounded_caster = bool(caster and len(obs.hostiles) <= 2 and assessment.simultaneous_attackers <= 2
            and caster.distance(obs.position) <= 2
            and all(enemy.distance(obs.position) <= 3 and info.melee <= (12 if enemy is caster else 18)
                    and (info.movement_speed < 1.5 or enemy is not caster and info.melee <= 3)
                    and (not info.ranged or info.ranged_max <= 15)
                    and not (info.poison or info.avoid_melee or info.escalates)
                    and (enemy is caster or not (info.ranged or info.disables or info.summons))
                    for enemy,info in movement_enemies)
            and (caster.distance(obs.position) <= 1 or any(
                caster.distance((obs.position[0]+DIRECTIONS[row[3]][0],
                                 obs.position[1]+DIRECTIONS[row[3]][1])) <= 1 for row in steps)))
        # A displayed minor-only book can justify one reversible approach, not
        # Berserk commitment to a blinking target. Keep every companion in the
        # forecast: a finite walking bound is not a permanent enclosure.
        minor_commitment = None
        minor_target = None
        minor_key = None
        minor_step = None
        minor_prepare = False
        summoners = [enemy for enemy,info in movement_enemies if info.summons]
        if retreat_exhausted and summoners:
            failures = []
            if len(summoners) != 1:
                failures.append("multiple summoners")
            else:
                minor_target = summoners[0]
                minor_key = obs.location, minor_target.name.casefold()
                info = self._observed_mechanics(obs,minor_target)
                hp_bound = public_hp_estimate(minor_target)
                if (minor_target.description is None or not minor_summoner(minor_target)
                        or hp_bound is None or not 0 < hp_bound <= 40
                        or info.melee > 12 or info.movement_speed >= 1.5
                        or info.ranged or info.disables or info.poison or info.escalates
                        or info.avoid_melee or visible_dangerous_weapon(minor_target)):
                    failures.append("unbounded examined book, body or weapon")
                if minor_target.distance(obs.position) != 2:
                    failures.append("target is not one visible step from contact")
            if (berserk or unseen_attack or obs.hp_ratio < .8
                    or any(mark in status for mark in
                           ("pois","slow","exh","-berserk","conf","tele","held","net","petr","paral","fire"))
                    or sum(count for count,_ in self.recent_hostiles.values()) > len(obs.hostiles)):
                failures.append("HP, status or unresolved-hostile reserve")
            duration = move_duration/self.config.horizon_actions
            one_standing = self.assess(obs,cells,covered,duration=duration)

            def separated_companions(risk):
                return all(enemy is minor_target or (
                    enemy.description is not None and not enemy.spells
                    and not (info.ranged or info.summons or info.disables or info.poison
                             or info.escalates or info.avoid_melee or visible_dangerous_weapon(enemy))
                    and threat.walking_steps_lower_bound is not None
                    and threat.walking_steps_lower_bound > info.reach + ceil(risk.horizon*info.movement_speed)
                    and threat.expected_loss == 0 and threat.burst_loss == 0)
                    for (enemy,info),threat in zip(movement_enemies,risk.threats))

            if not separated_companions(assessment):
                failures.append("another foe can contribute within the short horizon")
            if not failures:
                reserve = max(obs.hp_max*.35,2*self._observed_mechanics(obs,minor_target).melee)
                closing = []
                for _,_,_,key,after,_ in steps:
                    dx,dy = DIRECTIONS[key]
                    position = obs.position[0]+dx,obs.position[1]+dy
                    if minor_target.distance(position) != 1 or not separated_companions(after):
                        continue
                    one = self.assess(obs,cells,covered,position,duration=duration)
                    if (one.expected_loss < obs.hp*.2 and obs.hp-one.burst_loss >= reserve
                            and after.burst_loss < obs.hp*.8 and after.expected_loss < obs.hp*.35):
                        closing.append((one.expected_loss,one.burst_loss,key,one))
                if closing and minor_key not in self.minor_summoner_approaches:
                    _,_,key,one = min(closing)
                    minor_step = key
                    add("move",key,
                        "Take the encounter's single visible approach to an examined minor-only summoner; reserve two maximum melee hits, retain separated foes and reassess summons/Blink before another action",
                        "bounded summoner approach",38,one)
                else:
                    failures.append("single approach spent or no step preserves the damage reserve")
                hand_active = any("hand" in s.casefold() or "regen" in s.casefold() for s in obs.statuses)
                if (minor_step is not None and obs.hp < obs.hp_max and not hand_active
                        and self.ability(obs,"trogs_hand")
                        and minor_key not in self.minor_summoner_preparations
                        and one_standing.expected_loss < obs.hp*.2
                        and obs.hp-one_standing.burst_loss >= reserve):
                    minor_prepare = True
                    add("ability","trogs_hand",
                        "Prepare regeneration once before the bounded minor-summoner approach; failure does not authorize repeated preparation or Berserk pursuit",
                        "prepare minor summoner",19,one_standing,resource="piety")
            minor_commitment = {
                "target_id": minor_target.id if minor_target is not None else None,
                "public_identity": list(minor_key) if minor_key is not None else None,
                "approach_available": minor_step is not None,
                "preparation_available": minor_prepare,
                "rejected": failures,
                "uncertainty": "Summons and Blink require reobservation; no complete fight or permanent separation is promised"}
            rejected.extend("Minor-summoner approach refused: "+failure for failure in failures)
        shooter_step = None
        shooter_evidence = None
        lone_shooter = obs.hostiles[0] if len(obs.hostiles) == 1 else None
        prior_shooter_attempt = (lone_shooter is not None and (
            self.shooter_approach.active is not None
            or (obs.location,lone_shooter.name.casefold()) in self.shooter_approach.cancelled))
        shooter_controlled = (lone_shooter is not None and mechanics(lone_shooter).ranged
            and not short_caster_book(lone_shooter)
            and not mechanics(lone_shooter).summons and not berserk and (
                prior_shooter_attempt or self.shooter_approach.bounded_target(lone_shooter) and (
                    lone_shooter.distance(obs.position) > 2
                    and (mechanics(lone_shooter).movement_speed >= 1 or obs.hp_ratio >= .8)
                    or obs.hp_ratio >= .8 and not self.shooter_approach.restricted_status(status))))
        if shooter_controlled and not assessment.simultaneous_attackers:
            shooter_step,shooter_evidence = self.shooter_approach.plan(
                obs,cells,status=status,uncertain_contact=unseen_attack,
                unresolved=sum(count for count,_ in self.recent_hostiles.values()) > 1,
                assess=lambda position:self.assess(
                    obs,cells,covered,position,duration=move_time/self.config.horizon_actions))
            rejected.extend("Bounded shooter approach refused: "+reason
                            for reason in shooter_evidence["rejected"])
            if shooter_step is not None:
                add("move",shooter_step.key,
                    "Approach the examined bounded lone shooter once; preserve remaining expected-damage reserve, report stress sensitivity, verify actual distance progress and reassess before another action",
                    "bounded shooter approach",11 if self.shooter_approach.active is not None else 24,shooter_step.risk)
        # An admitted manageable lone fight is not a reason to evacuate a floor.
        # Keep defensive routing when the approach is refused or danger remains.
        route_needed = (self.withdrawal is not None or unseen_attack
            or shooter_controlled and (dangerous or shooter_step is None) or dangerous and (
            any(t.avoid_melee or t.disabling or t.summoning for t in assessment.threats)
            or obs.hp_ratio < .7 or assessment.simultaneous_attackers >= 2))
        can_withdraw = (not berserk or unseen_attack and not obs.hostiles
                        and (self.unseen_contact is None or self.unseen_attempted))
        if (route_needed and can_withdraw and not tele
                and (self.unseen_harm is None or unseen_attack)
                and not any(mark in status for mark in ("conf","petr","paral","held","constr","net"))):
            def route_cover(terrain,enemy,position):
                return ((obs.location,position,enemy.name) not in self.refuted_cover
                        and covered(terrain,enemy,position))
            unseen_loss = (self._unseen_loss_estimate(obs)
                           if any(reason.startswith("unseen incoming attack") for reason in assessment.reasons) else 0)
            plan = self._withdrawal_plan(obs,cells,visits,route_cover,unseen_loss=unseen_loss)
            if plan is not None:
                next_position = plan.path[0]
                key = next(key for key,(dx,dy) in DIRECTIONS.items()
                           if (obs.position[0]+dx,obs.position[1]+dy) == next_position)
                risk = replace(assessment,horizon=plan.horizon,
                               expected_loss=plan.expected_loss,burst_loss=plan.burst_loss)
                add("move",key,plan.reason+"; execute one step and reobserve",
                    "planned retreat",12,risk)
                withdrawal_evidence = asdict(plan)
            goal = self.withdrawal
            if (goal and goal.location == obs.location and obs.position == goal.position
                    and goal.kind == "upstairs" and not obs.hostiles and not unseen_attack
                    and (cell := cells.get(obs.position)) is not None and cell.feature in UPSTAIRS):
                preparation = self.stair_reevaluation.prepare_departure(
                    obs,unseen_danger=self.unseen_harm is not None or self.unseen_contact is not None)
                if preparation is not None:
                    add(preparation.kind,preparation.value,preparation.reason,preparation.objective,16)
                    stair_preparation_evidence = preparation.assessment["stair_reevaluation"]
                else:
                    add("stairs_up","","Finish the planned withdrawal through known upstairs; reobserve arrival, never assume it safe",
                        "planned retreat",16)

        if not berserk:
            if "pois" in status or confused:
                consume("quaff", ("potion of curing", "potions of curing"),
                        "Cure poison/confusion before losing safe options", 1 if confused or emergency else 8)
            if obs.hp < obs.hp_max and (emergency or obs.hp_ratio < .5 or (dangerous and obs.hp_ratio < .7)):
                consume("quaff", ("heal wounds",), "Heal before the next plausible damage burst", 3)
            if emergency and obs.hp_ratio < .45:
                consume("quaff", ("potion of curing", "potions of curing"), "Use available curing rather than die with it unused", 5)

        if any(s.casefold() == "fire" for s in obs.statuses):
            if steps:
                fire_steps = steps
                if berserk and obs.hostiles and not any(threat.avoid_melee for threat in assessment.threats):
                    forward = [row for row in steps
                               if row[4].simultaneous_attackers <= max(1,assessment.simultaneous_attackers)
                               and row[4].expected_loss <= assessment.expected_loss+obs.hp*.1]
                    if forward:
                        fire_steps = sorted(forward,key=lambda row: (
                            min(enemy.distance((obs.position[0]+DIRECTIONS[row[3]][0],
                                                obs.position[1]+DIRECTIONS[row[3]][1])) for enemy in obs.hostiles),
                            row[0]))
                _, _, _, key, after, _ = fire_steps[0]
                add("move", key, "Shed liquid fire on clear visible ground; preserve a bounded Berserk approach when possible", "emergency recovery", 7, after)
            else:
                add("pause", "", "Liquid fire has no legal clear movement square", "blocked", 97)

        if dangerous:
            if any(t.avoid_melee or t.disabling or t.summoning for t in assessment.threats):
                self._remember(obs, "; ".join(assessment.reasons) or assessment.category)
            if not berserk:
                if not no_scroll:
                    blink = self.item(obs, "blinking", kind="read")
                    if blink and emergency:
                        targets = [(self.assess(obs, cells, covered, (cell.x, cell.y), duration=1.), cell)
                                   for cell in obs.cells if cell.visible and cell.walkable and not cell.cloud
                                   and cell.feature not in CLOSED_DOORS and (cell.x, cell.y) not in enemy_positions
                                   and all(m.distance((cell.x, cell.y)) >= 3 for m in obs.hostiles)]
                        if targets:
                            after, cell = min(targets, key=lambda row: (
                                row[0].expected_loss,
                                -min((m.distance((row[1].x,row[1].y)) for m in obs.hostiles),
                                     default=max(abs(row[1].x-obs.position[0]),abs(row[1].y-obs.position[1])))))
                            if (after.expected_loss < assessment.expected_loss
                                    or unseen_attack and max(abs(cell.x-obs.position[0]),abs(cell.y-obs.position[1])) >= 3):
                                reason = ("Blink to distant clear ground after unseen damage; reassess"
                                          if unseen_attack else "Blink to verified visible separation; walking permits pursuit attacks")
                                add("blink", f"{blink.letter}:{cell.x},{cell.y}", reason, "retreat", 0, after, "blinking")
                    if emergency and assessment.simultaneous_attackers >= 2:
                        consume("read", ("scroll of fear", "scrolls of fear"), "Disperse simultaneous attackers before another burst", 4)
                    escape_pressure = (emergency or assessment.category == "avoid" or unseen_attack
                                       or assessment.simultaneous_attackers >= 2
                                       or any(t.disabling or t.summoning for t in assessment.threats))
                    if not tele and (obs.hostiles or unseen_attack) and escape_pressure:
                        one_turn_burst = assessment.burst_loss / max(1.,assessment.horizon) * (1.5 if "slow" in status else 1.)
                        priority = 2 if one_turn_burst < obs.hp * .8 else 10
                        consume("read", ("teleportation",), "Start delayed teleport early; preserve healing for its countdown", priority)
                    if sum(t.ranged for t in assessment.threats) >= 1 or unseen_attack:
                        consume("read", ("scroll of fog", "scrolls of fog"), "Break incoming ranged lines while preserving escape options", 12)
                summon_space = any(cell.visible and cell.walkable and not cell.cloud
                                   and cell.feature not in CLOSED_DOORS
                                   and 0 < max(abs(cell.x-obs.position[0]),abs(cell.y-obs.position[1])) <= 2
                                   and (cell.x,cell.y) not in enemy_positions for cell in obs.cells)
                support_goal = 2 if emergency and any(t.avoid_melee for t in assessment.threats) else 1
                if (self.ability(obs, "brothers_in_arms") and (obs.hostiles or unseen_attack)
                        and summon_space and len(support) < support_goal):
                    add("ability", "brothers_in_arms", "Spend piety now: an ally can absorb and deal damage in this unacceptable fight", "summon support", 6, resource="piety")
                hand_active = any("hand" in s.casefold() or "regen" in s.casefold() for s in obs.statuses)
                if (self.ability(obs, "trogs_hand") and not hand_active
                        and (any(mechanics(enemy).will_check for enemy in obs.hostiles)
                             and any(t.disabling for t in assessment.threats) or obs.hp_ratio < .8
                             or unseen_attack and self.unseen_harm is not None
                             and self.unseen_harm[1] == obs.turn)):
                    will_risk = any(mechanics(enemy).will_check for enemy in obs.hostiles) and any(
                        threat.disabling for threat in assessment.threats)
                    add("ability", "trogs_hand", "Prepare regeneration and two Will pips before disabling magic or further attrition", "prepare", 9 if will_risk else 13, resource="piety")
                stair = self._return_stair_escape(obs,cells,covered,assessment,move_time)
                if stair is not None:
                    risk,stair_evidence = stair
                    add("escape_upstairs","","Use the observed return stair with scored departure exposure; no observed adjacent followers, but arrival changes and summoning remain uncertain",
                        "retreat",11,risk)
                corner = self._corner_step(obs, cells, visits, covered, assessment)
                if corner is not None:
                    dx, dy = DIRECTIONS[corner]
                    after = self.assess(obs, cells, covered, (obs.position[0] + dx, obs.position[1] + dy), duration=1.)
                    add("move", corner, "Reposition toward apparent cover; verify LOS after this single step", "retreat", 14, after)
            must_disengage = (any(refuses_melee(t) or (t.summoning or t.disabling) and not bounded_caster
                                  for t in assessment.threats)
                              or assessment.simultaneous_attackers >= 3 or "pois" in status)
            if not berserk or must_disengage:
                for _, _, _, key, after, effective in steps:
                    if effective and not retreat_exhausted:
                        add("move", key, "Reposition to improve cover or formation; prefer visited ground and account for pursuit", "retreat", 15, after)
                        break
            if berserk:
                rejected.append("Consumables and divine abilities are unavailable while Berserk")

        if held:
            key = steps[0][3] if steps else next(iter(DIRECTIONS))
            add("move", key, "Struggle against the visible net/web; native movement spends this turn on release, not displacement",
                "escape restraint", 12)

        adjacent = [m for m in obs.hostiles if m.distance(obs.position) <= 1]
        # A conservative forecast or native High label can call even a weak
        # ordinary foe dangerous. Neither alone justifies Berserk's commitment
        # and aftermath: require substantial public attack damage, a genuinely
        # heavy group, or significant observed HP loss.
        # These fractions are policy bounds, not predictions of actual hits.
        difficult_fight = (
            any(max(info.melee, info.ranged_max if info.ranged else 0) >= obs.hp * .4
                for _, info in movement_enemies)
            or len(obs.hostiles) > 1 and sum(info.melee for _, info in movement_enemies) >= obs.hp * .5
            or assessment.recent_damage >= obs.hp_max * .2)
        visible_names = {enemy.name.casefold() for enemy in obs.hostiles}
        poison_allowance = (max(12,obs.hp_max*.3) if any(mechanics(enemy).poison for enemy in obs.hostiles) else 0)
        small_wave = (len(obs.hostiles) <= 3 and 1 <= assessment.simultaneous_attackers <= 2
                      and sum(count for count,_ in self.recent_hostiles.values()) <= len(obs.hostiles)
                      and sum(public_hp_estimate(enemy) or obs.hp_max for enemy in obs.hostiles) <= obs.hp*2)
        ordinary_wave = ((len(adjacent) <= 1 and current_front == 1
                          and assessment.simultaneous_attackers == 1 or small_wave)
                         and 1 < sum(count for count,_ in self.recent_hostiles.values()) <= 5
                         and all(name in visible_names for name in self.recent_hostiles)
                         and sum(mechanics(enemy).poison for enemy in obs.hostiles) <= 1
                         and obs.hp-poison_allowance > max((mechanics(enemy).melee for enemy in obs.hostiles),default=0)
                         and all(not (info.ranged or info.disables or info.summons
                                      or info.avoid_melee or info.escalates or info.ally_reposition or info.ally_might)
                                 and (not info.poison or info.melee <= 6)
                                 and info.melee <= min(18,obs.hp*.7)
                                 for info in (mechanics(enemy) for enemy in obs.hostiles)))
        close_commitment = ((bool(obs.hostiles) and all(enemy.distance(obs.position) <= 2
                             and mechanics(enemy).movement_speed < 1.5 for enemy in obs.hostiles))
                            or ordinary_wave or bounded_caster)
        bounded_group = (len(obs.hostiles) <= 2 or (
            len(obs.hostiles) == 3 and emergency and sum(mechanics(enemy).melee for enemy in obs.hostiles) <= obs.hp*1.3)) or ordinary_wave
        all_recent_visible = (sum(count for count,_ in self.recent_hostiles.values()) <= len(obs.hostiles)
                              or ordinary_wave)
        bounded_front = (bool(adjacent) and len(obs.hostiles)<=2 and all(
            threat.distance<=2 or (threat.ranged and threat.movement_speed<1 and threat.expected_loss<obs.hp*.2)
            for threat in assessment.threats))
        safe_commitment = (bounded_group and all_recent_visible and (len(adjacent) == len(obs.hostiles) or close_commitment or bounded_front)
                           and not any(t.avoid_melee or (t.summoning or t.disabling) and not bounded_caster
                                       or t.entangling and t.distance > 1
                                       or t.ranged and t.movement_speed >= 1.5 for t in assessment.threats)
                           and not held
                           and not any(mark in status for mark in ("pois", "slow", "exh", "-berserk", "conf", "tele"))
                           and obs.hp_ratio >= self.config.berserk_min_hp and assessment.expected_loss < obs.hp * 1.8)
        if (adjacent or close_commitment) and not berserk and self.ability(obs, "berserk"):
            if not difficult_fight:
                rejected.append("Berserk reserved: this ordinary fight does not justify its commitment and recovery")
            elif safe_commitment and dangerous:
                priority = 8 if emergency or ordinary_wave else 13 if any(t.ranged for t in assessment.threats) else 18
                reason = ("Commit early to a bounded ordinary wave with at most two current contacts; reserve HP for at most one weak poison source, retain the known queue and reassess every swing"
                          if ordinary_wave else
                          "Commit to a near low-HP caster with only displayed Dart/Slow/Call Imp spells and at most one weak companion; no blink, invisibility or hard disabling; preserve the aftermath"
                          if bounded_caster else
                          "Commit early to a bounded nearby fight; exclude ongoing poison, dangerous kiting, summons and disabling threats; preserve the aftermath")
                add("ability", "berserk", reason, "combat", priority, resource="berserk commitment")
            else:
                rejected.append("Berserk refused: escape, poison, restraint, distant web control, additional threats or exhaustion risk makes commitment unsafe")
        if (dangerous and difficult_fight and not berserk and not held and not unseen_attack
                and self.unseen_harm is None and self.unseen_contact is None
                and not self.unseen_displacements.active(obs) and len(obs.hostiles)==1
                and sum(count for count,_ in self.recent_hostiles.values())<=1
                and self.ability(obs,"berserk")
                and obs.hp_ratio>=max(.75,self.config.berserk_min_hp)
                and not any(mark in status for mark in
                            ("pois","slow","exh","-berserk","conf","tele","petr","paral","net"))):
            leader = obs.hostiles[0]
            info = self._observed_mechanics(obs,leader)
            support_book = {spell.casefold() for spell in leader.spells}
            if (leader.description is not None and leader.distance(obs.position)==3
                    and info.ally_reposition and info.ally_might
                    and support_book <= {"throw ally","battlecry"}
                    and 0<(public_hp_estimate(leader) or 0)<=20
                    and info.melee<=12 and info.ranged_max<=3 and info.movement_speed<1.5
                    and not (info.disables or info.summons or info.poison or info.escalates
                             or info.avoid_melee or visible_dangerous_weapon(leader))):
                occupied = {(enemy.x,enemy.y) for enemy in obs.monsters}
                staging = {(cell.x,cell.y) for cell in obs.cells
                           if cell.visible and cell.walkable and not cell.cloud
                           and cell.feature not in CLOSED_DOORS and (cell.x,cell.y) not in occupied
                           and leader.distance((cell.x,cell.y))<=1}
                near = False
                for _,_,_,key,_,_ in steps:
                    dx,dy = DIRECTIONS[key]
                    first = obs.position[0]+dx,obs.position[1]+dy
                    if cells[first].feature in CLOSED_DOORS:
                        continue
                    if any(max(abs(position[0]-first[0]),abs(position[1]-first[1]))<=1
                           for position in staging):
                        near = True
                        break
                activation = self.assess(obs,cells,covered,duration=1/self.config.horizon_actions)
                reserve = max(obs.hp_max*.4,2*info.melee)
                if (near and activation.burst_loss<obs.hp*.2
                        and int(obs.hp*BERSERK_HP_MULTIPLIER)-activation.burst_loss>=reserve):
                    add("ability","berserk",
                        "Prepare before the lone examined low-HP ally-throwing leader delivers support; current clear approach takes at most two steps, reserve the aftermath and reassess new bodies; no hidden ally or active buff assumed",
                        "combat preparation",27,activation,resource="berserk commitment")
        failed_walk = self._retreat_moved and adjacent
        if (emergency and (not berserk or must_disengage) and adjacent
                and not failed_walk and not tele and not retreat_exhausted and not faster_pursuit):
            for _, _, _, key, after, _ in steps:
                dx, dy = DIRECTIONS[key]
                position = obs.position[0] + dx, obs.position[1] + dy
                if visits[position] > 0 and min(m.distance(position) for m in obs.hostiles) > 1:
                    add("move", key, "Try one known separation route rather than accept a likely fatal exchange; pursuit can still hit",
                        "retreat", 35, after)
                    break

        if adjacent:
            target = min(adjacent, key=lambda m: (mechanics(m).avoid_melee, -m.threat, m.name))
            forbidden = next((refuses_melee(t) for t in assessment.threats if t.position == (target.x,target.y)), False)
            wielded_axe = any(item.equipped and axe_type(public_item_name(item.name))
                              for item in obs.inventory)
            if wielded_axe:
                cleave_positions = {(enemy.x, enemy.y) for enemy in adjacent}
                forbidden = forbidden or any(refuses_melee(t) and t.position in cleave_positions
                                             for t in assessment.threats)
            if not forbidden:
                if not berserk and dangerous and not tele:
                    if not any(s.casefold() == "might" for s in obs.statuses):
                        consume("quaff", ("potion of might", "potions of might"), "Use might while retaining consumables and escape options", 24)
                    if (not any(s.casefold() == "resist" for s in obs.statuses)
                            and any("ice" in t.name.casefold() or "dragon" in t.name.casefold() for t in assessment.threats)):
                        consume("quaff", ("resistance",), "Prepare elemental resistance before a dangerous exchange", 23)
                key = next(key for key, delta in DIRECTIONS.items()
                           if delta == (target.x-obs.position[0], target.y-obs.position[1]))
                reason = (f"Continue the existing Berserk commitment against examined {target.name} once; high risk remains, with no kill or survival guarantee"
                          if (target.x,target.y) in committed_casters else
                          f"Attack {target.name} once; reassess fresh damage and formation")
                add("move", key, reason, "combat", 40 if dangerous else 30)
            else:
                rejected.append(f"Refuse melee against {target.name}: target or adjacent axe-cleave victim forbids melee")

        if obs.hostiles and not berserk:
            target = max(obs.hostiles, key=lambda m: (mechanics(m).avoid_melee, m.threat, -m.distance(obs.position)))
            warded_range = target.distance(obs.position)>1 and mechanics(target).damage_requires_adjacency
            wand_names = (("wand of roots","wand of acid") if warded_range else
                          ("wand of acid","wand of iceblast","wand of flame","wand of mindburst","wand of roots"))
            wand = self.item(obs,*wand_names,kind="zap")
            if wand and re.search(r"\([1-9]\d*\)", wand.name) and not any(m.attitude != 0 for m in obs.monsters):
                purpose = "Use known non-damaging control; nonadjacent HP damage is warded" if warded_range else "Use known evocable instead of unsafe melee"
                add("zap", f"{wand.letter}:{target.x},{target.y}", f"{purpose}: {target.name}", "ranged control", 20 if dangerous else 35, resource="wand charge")
            missile_names = (("curare-tipped dart","atropa-tipped dart") if warded_range else
                             ("javelin","boomerang","dart"))
            missile = self.item(obs,*missile_names,kind="throw")
            if missile and not adjacent and target.distance(obs.position) <= 5 and not any(m.attitude != 0 for m in obs.monsters):
                add("throw", f"{missile.letter}:{target.x},{target.y}", f"Use known missile while {target.name} approaches", "ranged attack", 32)

        if obs.hostiles and not adjacent:
            if not dangerous and not berserk and self.lure_steps < self.config.lure_step_limit and obs.turn - self.encounter_start < 12:
                corner = self._corner_step(obs, cells, visits, covered, assessment)
                if corner is not None:
                    dx, dy = DIRECTIONS[corner]
                    after = self.assess(obs, cells, covered, (obs.position[0]+dx, obs.position[1]+dy), duration=1.)
                    add("move", corner, "Lure a shooter around nearby visible cover instead of trading ranged damage", "lure", 25, after)
            for _, _, _, key, after, effective in steps:
                if (not dangerous and not retreat_exhausted and not berserk and effective and after.expected_loss + 1 < assessment.expected_loss
                        and self.lure_steps < self.config.lure_step_limit and obs.turn - self.encounter_start < 12):
                    add("move", key, "Lure toward a known corner instead of charging the group", "lure", 26, after)
                    break
            slow_shooter = (len(obs.hostiles) == 1 and mechanics(obs.hostiles[0]).movement_speed < 1
                            and any(t.ranged for t in assessment.threats)
                            and not any(t.avoid_melee or t.disabling or t.summoning for t in assessment.threats)
                            and not mechanics(obs.hostiles[0]).poison
                            and not any(mark in status for mark in ("pois", "slow", "exh", "-berserk", "conf", "tele"))
                            and not emergency)
            may_close = berserk or (not shooter_controlled and (
                (obs.hp_ratio >= .75 and not dangerous) or slow_shooter))
            if may_close:
                # Closing on a slower lone shooter can reduce future exposure;
                # waiting under fire is not a defence just because HP fell below
                # the routine commitment threshold.
                shooters = [m for m in obs.hostiles if mechanics(m).ranged]
                reaching = [m for m in obs.hostiles if m.distance(obs.position) == 2
                            and self.witnessed_reach(obs, m)]
                stalled_approach = not dangerous and self.encounter_start >= 0 and obs.turn-self.encounter_start >= 4
                targets = [monster for monster in obs.hostiles
                           if monster in reaching or (berserk and any(
                               t.position == (monster.x,monster.y) and not refuses_melee(t)
                               for t in assessment.threats))
                           or (not berserk and len(obs.hostiles) == 1 and (shooters or stalled_approach))]
                for target in sorted(targets, key=lambda monster: (monster.distance(obs.position), -monster.threat)):
                    closing = [row for row in steps
                               if target.distance((obs.position[0]+DIRECTIONS[row[3]][0], obs.position[1]+DIRECTIONS[row[3]][1])) < target.distance(obs.position)
                               and all(monster.id == target.id or monster.distance((
                                   obs.position[0]+DIRECTIONS[row[3]][0], obs.position[1]+DIRECTIONS[row[3]][1])) > 1
                                       for monster in obs.hostiles)]
                    if slow_shooter:
                        standing = self.assess(obs, cells, covered, duration=1.)
                        # Bound the exposure of this single movement, not three
                        # future attacks after we can already strike the shooter.
                        one_step_burst = mechanics(target).melee * ceil(mechanics(target).speed*move_time)
                        closing = [row for row in closing
                                   if row[4].expected_loss <= standing.expected_loss
                                   and one_step_burst < obs.hp * .8]
                    if closing and (berserk or reaching or stalled_approach
                                    or any(t.ranged for t in assessment.threats)
                                    or obs.turn-self.encounter_start >= 3):
                        _, _, _, key, after, _ = closing[0]
                        add("move", key, "Close one visible step on a manageable attacker without recruiting another adjacent enemy", "combat", 36, after)
                        break
                if (self.encounter_progress.may_wait and not any(t.ranged for t in assessment.threats)
                        and not assessment.simultaneous_attackers):
                    add("wait", "", "Let manageable melee enemies approach the defended position", "lure", 45)
        # A weak adjacent body must not hide a distant foe's public weapon/book.
        # Inspecting is free and bounded per native instance, but an already
        # actionable urgent recovery/escape/defence still takes precedence.
        urgent_response = any(
            candidate.priority <= 20
            and not (candidate.action.kind == "ability" and candidate.action.value == "berserk")
            and (candidate.action.objective == "emergency recovery"
                 or emergency and candidate.resource != "none")
            for candidate in candidates)
        if (obs.hostiles and not urgent_response
                and not any(s.casefold() == "fire" for s in obs.statuses)):
            missing = (monster for monster in obs.hostiles
                       if monster.description is None
                       and (obs.location, monster.id) not in self.inspected_monsters)
            monster = max(missing, key=lambda enemy: (enemy.threat, -enemy.distance(obs.position)),
                          default=None)
            if monster is not None:
                self.inspected_monsters.add((obs.location, monster.id))
                add("inspect_monster", f"{monster.x},{monster.y}",
                    "Read the visible creature description before committing; no game turn passes",
                    "assess threat", -1)

        if unseen_attack and self.unseen_contact is not None:
            last_seen, seen_turn = self.unseen_contact
            target_position = last_seen.x, last_seen.y
            target_cell = cells.get(target_position)
            if (not self.unseen_attempted and obs.turn <= seen_turn + 2
                    and last_seen.distance(obs.position) == 1
                    and target_cell and target_cell.visible and target_cell.walkable and not target_cell.cloud
                    and target_position not in enemy_positions and not mechanics(last_seen).avoid_melee
                    and not visible_dangerous_weapon(last_seen)
                    and not any(t.avoid_melee for t in assessment.threats)
                    and not any(mark in status for mark in ("conf", "petr", "paral"))):
                key = next(key for key, delta in DIRECTIONS.items()
                           if delta == (last_seen.x-obs.position[0], last_seen.y-obs.position[1]))
                add("attack", key, "Attack the last publicly seen adjacent square once without moving; its current occupancy is unknown",
                    "defend unseen", 40)
            if not retreat_exhausted:
                for _, _, _, key, after, _ in steps:
                    dx, dy = DIRECTIONS[key]
                    position = obs.position[0]+dx, obs.position[1]+dy
                    if visits[position] and last_seen.distance(position) > last_seen.distance(obs.position):
                        add("move", key, "Withdraw once on known clear ground away from the last seen attacker; reassess unseen harm",
                            "retreat", 50, after)
                        break
        if (unseen_attack and (self.unseen_melee_turn == (obs.location,obs.turn)
                               or self.unseen_search.active(obs))
                and not any(mark in status for mark in ("conf","petr","paral","asleep"))
                and not any(t.avoid_melee for t in assessment.threats)
                and not any(mechanics(enemy).avoid_melee or mechanics(enemy).escalates
                            or visible_dangerous_weapon(enemy)
                            for enemy,_ in self.last_seen_hostiles.values())):
            key = self.unseen_search.choose(
                obs,cells,search_all=self.unseen_melee_turn == (obs.location,obs.turn))
            if key is not None:
                add("attack",key,"Search one clear adjacent square with native Ctrl-direction, without moving; no invisible position or identity is assumed",
                    "defend unseen",41)
        if tele and (obs.hostiles or unseen_attack) and not candidates:
            add("wait", "", "Teleport is already pending; defend without restarting the countdown", "await teleport", 70)
        small_book = None
        if not candidates and not unseen_attack:
            small_book = self._small_book_fallback(obs,cells,covered,steps,move_duration)
            if small_book is not None:
                candidates.append(small_book[0])
        guarded_casters = {enemy.id for enemy,info in movement_enemies
                           if short_caster_book(enemy) and public_hp_estimate(enemy) is not None
                           and public_hp_estimate(enemy) <= 25
                           and ((obs.will or 0) >= 2 or "will++" in status)}
        approachable_casters = {enemy.id for enemy in short_casters}
        prepared_minor_group = (bool(approachable_casters) and len(obs.hostiles) <= 2
            and sum(count for count,_ in self.recent_hostiles.values()) <= len(obs.hostiles)
            and all(info.melee <= 18 and not (info.poison or info.avoid_melee or info.escalates)
                    and (enemy.id in approachable_casters or not (info.ranged or info.disables or info.summons))
                    for enemy,info in movement_enemies))
        if (obs.hostiles and not adjacent and not candidates
                and not any(t.avoid_melee for t in assessment.threats)
                and all(not info.ranged or enemy.id in guarded_casters for enemy,info in movement_enemies)
                and not assessment.simultaneous_attackers):
            summoners = [enemy for enemy,info in movement_enemies if info.summons]
            guarded_minor = (not berserk and not unseen_attack and obs.hp_ratio >= .8
                and len(obs.hostiles) <= 3 and self.lure_steps < self.config.lure_step_limit
                and sum(count for count,_ in self.recent_hostiles.values()) <= len(obs.hostiles)
                and not any(mark in status for mark in ("pois","slow","exh","conf","tele","held","net"))
                and all(minor_summoner(enemy) or enemy.id in guarded_casters for enemy in summoners)
                and all(not (info.poison or info.escalates) and (not info.disables or enemy.id in guarded_casters)
                        and info.melee <= 18 for enemy,info in movement_enemies))
            if self.encounter_progress.may_wait and ((not summoners and not guarded_casters) or guarded_minor):
                one = self.assess(obs,cells,covered,duration=move_time/self.config.horizon_actions)
                if (not summoners and not guarded_casters or one.expected_loss < obs.hp*(.2 if guarded_casters else .1)
                        and one.burst_loss < obs.hp*(.5 if guarded_casters else .3)):
                    reason = ("Hold one bounded turn under prepared Will against the displayed low-HP Dart/Slow book; reassess ranged damage and new summons"
                              if guarded_casters else
                              "Hold one guarded turn to peel approaching minor summons; new summons remain uncertain and require reassessment"
                              if summoners else "Hold the defended tile and let the ordinary melee group approach one at a time")
                    add("wait","",reason,"defend choke",75,one)
        # Reserve all remaining publicly estimated poison damage, not just the
        # next tick. Curing has already had priority; mild poison alone must not
        # remove every bounded ordinary defence. Unknown/lethal poison does not
        # authorize this fallback, and Berserk retains its separate prohibition.
        survival_hp = obs.hp
        if "pois" in status:
            survival_hp = min(obs.hp, obs.poison_survival or 0)
        if (obs.hostiles and not adjacent and not candidates and not shooter_controlled
                and not any(threat.avoid_melee for threat in assessment.threats)
                and (not any(threat.summoning for threat in assessment.threats) or prepared_minor_group)
                and not any(mark in status for mark in ("conf","petr","paral"))
                and ("pois" not in status or survival_hp > max(5,obs.hp_max*.2))):
            # Resources, useful retreat and defended waits have already been
            # considered. Ordinary mixed encounters still need a legal action:
            # a three-action danger forecast is not a reason to freeze when a
            # single observed action fits the HP remaining after poison.
            duration = move_time/self.config.horizon_actions/(1.5 if "slow" in status else 1.)
            standing = self.assess(obs,cells,covered,duration=duration)
            approaching = any(not mechanics(enemy).ranged and enemy.distance(obs.position) <= 3
                              for enemy in obs.hostiles)
            standing_fronts = reaction_fronts(obs.position)
            imminent = any(not info.ranged and enemy.distance(obs.position)>info.reach
                           and walking.distance(enemy,info,obs.position,
                               info.reach+ceil(move_time*info.movement_speed-1e-9))
                               <= info.reach+ceil(move_time*info.movement_speed-1e-9)
                           for enemy,info in movement_enemies)
            # A lone visible support caster has no observed ally formation to
            # preserve. Permit the existing bounded one-step defence, not a
            # forced Berserk; new/unresolved companions still invalidate it.
            formation_clear = (not unseen_attack and self.unseen_contact is None
                and sum(count for count,_ in self.recent_hostiles.values())<=len(obs.hostiles)
                and (len(movement_enemies) == 1
                     or not any(info.ally_reposition or info.ally_might for _,info in movement_enemies)))
            hold_safe = formation_clear and not any(info.disables for _,info in movement_enemies)
            imminent_hold = imminent and 0<standing_fronts<=2 and not any(
                info.summons for _,info in movement_enemies)
            if (self.encounter_progress.may_wait and not berserk and len(obs.hostiles)>1 and approaching
                    and hold_safe and standing_fronts<=2
                    and (obs.turn-self.encounter_start < self.config.lure_step_limit or imminent_hold)
                    and standing.expected_loss < survival_hp*.2 and standing.burst_loss < survival_hp*.7):
                add("wait","",f"Hold formation for at most {standing_fronts} possible next-reaction contact fronts; reassess limited ranged exposure",
                    "defend choke",80,standing)
            else:
                closing = []
                for _,_,_,key,after,_ in steps:
                    dx,dy = DIRECTIONS[key]
                    position = obs.position[0]+dx,obs.position[1]+dy
                    contacts = reaction_fronts(position)
                    # A defensive step may reduce several possible arrivals to
                    # one without approaching any enemy. It authorizes no route
                    # and reserves the same hard uncertainty bounds as a hold.
                    reforming = (not berserk and hold_safe and standing_fronts>2 and contacts<=1
                        and not any(mark in status for mark in ("slow","exh","tele","held","net"))
                        and all(enemy.description is not None
                                and not (info.poison or info.escalates or info.summons)
                                for enemy,info in movement_enemies))
                    if (not hold_safe or contacts>2 or not (reforming or any(
                            enemy.distance(position)<enemy.distance(obs.position) for enemy in obs.hostiles))):
                        continue
                    one = self.assess(obs,cells,covered,position,duration=duration)
                    # A fast foe may approach into contact during this move.
                    # Reserve a possible opportunity strike when pulling away
                    # inside that reach/travel bound; do not assign it a future
                    # square or treat the rest of a route as authorized.
                    leaving = [(enemy,info) for enemy,info in movement_enemies
                               if enemy.distance(position) > enemy.distance(obs.position)
                               and enemy.distance(position) <= max(
                                   info.reach,2 if self.witnessed_reach(obs,enemy) else 1)
                                   + ceil(move_time*info.movement_speed-1e-9)]
                    one = replace(one,
                        expected_loss=one.expected_loss+sum(info.melee*OPPORTUNITY_CHANCE for _,info in leaving),
                        burst_loss=one.burst_loss+sum(info.melee for _,info in leaving))
                    if (one.expected_loss < survival_hp*(.2 if contacts==2 or reforming else .35)
                            and one.burst_loss < survival_hp*(.6 if contacts==2 or reforming else .9)):
                        closing.append((one.expected_loss,one.burst_loss,front_capacity(position),
                                        -visits[position],key,one,reforming))
                if closing:
                    _,_,_,_,key,after,reforming = min(closing)
                    reason = ("Reform one visible step from multiple possible arrivals to at most one contact; reserve poison damage and reassess without assuming an escape route or freedom from webs"
                              if reforming else
                              "Advance one visible step with bounded projected exposure; prefer a single contact, permit two only at a lower risk budget; reassess instead of freezing")
                    add("move",key,reason,"retreat" if reforming else "combat",80,after)
        if (len(obs.hostiles) == 1 and not berserk and not unseen_attack
                and self.unseen_contact is None and self.ability(obs,"berserk")
                and not any(mark in status for mark in ("pois","slow","exh","-berserk","conf","tele","held","net"))
                and sum(count for count,_ in self.recent_hostiles.values()) <= 1
                and all(candidate.action.kind == "ability" and candidate.action.value == "trogs_hand"
                        for candidate in candidates)):
            lone = obs.hostiles[0]
            info = mechanics(lone)
            if (lone.description is not None and lone.distance(obs.position) <= 2
                    and info.melee <= 18 and int(obs.hp*BERSERK_HP_MULTIPLIER) > info.melee
                    and info.movement_speed < 1.5
                    and not any(t.avoid_melee or t.ranged or t.disabling or t.summoning for t in assessment.threats)
                    and not (info.poison or info.escalates)
                    and (adjacent or any(lone.distance((obs.position[0]+DIRECTIONS[row[3]][0],
                                                       obs.position[1]+DIRECTIONS[row[3]][1])) <= 1 for row in steps))):
                add("ability","berserk","Risky last-resort ordinary duel: no credible escape or resource; rage HP can survive one estimated maximum strike, not a guarantee of the fight",
                    "combat",12,resource="last-resort Berserk commitment")
        if (not candidates and unseen_attack
                and (berserk or any(mark in status for mark in ("conf","petr","paral","asleep")))):
            add("wait","","Wait one constrained turn: native status prevents controlled movement or resource use; reassess immediately",
                "recover control",60)
        if not candidates and unseen_attack and not obs.hostiles:
            evidence_turn = max(
                self.unseen_harm[1] if self.unseen_harm else -1,
                self.unseen_contact[1] if self.unseen_contact else -1,
                self.unseen_search.confirmed[1] if self.unseen_search.confirmed else -1,
                max((entry["turn"] for entry in displacement_evidence),default=-1))
            if evidence_turn < obs.turn and self.recent_damage(obs) == 0:
                add("wait","","Observe one quiet turn while old unseen evidence expires; fresh incoming danger interrupts this bounded wait",
                    "verify unseen",60)
        if obs.hostiles and not candidates:
            add("pause", "", "No credible legal attack or escape: preserve the game for inspection", "blocked", 99)
        if unseen_attack and not candidates:
            add("pause", "", "Unseen attack or uncertain contact has no credible known defence: stop routine exploration for inspection", "blocked", 99)
        if not obs.hostiles and not unseen_attack and "dangerous status restricts responses" in assessment.reasons:
            add("wait", "", "Let disabling or corrosive status expire before routine exploration", "recover", 40)
        if not obs.hostiles and not unseen_attack and obs.turn < self.hold_until:
            add("wait", "", "Hold briefly after hostile contact ends; reassess before reopening exploration", "lure", 60)
        if (support and obs.hostiles and not berserk
                and all(enemy.distance(obs.position) >= mechanics(enemy).reach+2
                        and not mechanics(enemy).ranged for enemy in obs.hostiles)
                and any(ally.distance((enemy.x,enemy.y)) <= 1 for ally in support for enemy in obs.hostiles)):
            add("wait", "", "Let the visible ally engage while remaining outside immediate melee reach; reassess one turn", "support ally", 28)
        if not candidates:
            self.previous = obs
            return None
        chosen = min(candidates, key=lambda candidate: (candidate.priority, candidate.expected_loss, candidate.burst_loss))
        if chosen.action.objective == "bounded shooter approach":
            self.shooter_approach.commit(obs)
        if minor_key is not None:
            if chosen.action.objective == "bounded summoner approach":
                self.minor_summoner_approaches.add(minor_key)
            elif chosen.action.objective == "prepare minor summoner":
                self.minor_summoner_preparations.add(minor_key)
        if small_book is not None and chosen is small_book[0]:
            key = small_book[1]
            if chosen.action.objective == "bounded caster close":
                distance = next(enemy.distance(obs.position) for enemy in obs.hostiles
                                if (obs.location,enemy.name.casefold()) == key)
                remaining,_ = self.short_caster_closes.get(key,(distance-1,distance+1))
                self.short_caster_closes[key] = remaining-1,distance
            elif chosen.action.objective == "intercept minor summon":
                self.minor_summon_interceptions.add(key)
            else:
                self.minor_summoner_preparations.add(key)
        if chosen.action.objective == "defend unseen":
            self.unseen_attempted = True
            if chosen.action.kind == "attack":
                self.unseen_search.attempted.add(chosen.action.value)
        if chosen.action.objective in {"lure", "retreat"} and chosen.action.kind == "move":
            self.lure_steps += 1
        if chosen.action.objective == "defend choke" and (guarded_casters or any(info.summons for _,info in movement_enemies)):
            self.lure_steps += 1
        evidence = {"category": assessment.category, "horizon": assessment.horizon,
                    "assessment": asdict(assessment),
                    "policy": asdict(self.config),
                    "policy_state": {"ineffective_retreats": self.ineffective_retreats,
                                     "recent_unresolved_hostiles": {name: count for name,(count,_) in self.recent_hostiles.items()}},
                    "candidates": [{"kind": candidate.action.kind, "value": candidate.action.value,
                                    "priority": candidate.priority, "expected_loss": candidate.expected_loss,
                                    "burst_loss": candidate.burst_loss, "resource": candidate.resource}
                                   for candidate in candidates], "rejected": rejected,
                    "chosen": {"kind": chosen.action.kind, "value": chosen.action.value},
                    "estimate_kind": "conservative public-mechanics heuristic, not exact hit prediction"}
        if displacement_evidence:
            evidence["unseen_displacements"] = displacement_evidence
        if minor_commitment is not None:
            evidence["minor_summoner_commitment"] = minor_commitment
        if small_book is not None:
            evidence["small_book_action"] = small_book[2]
        if shooter_evidence is not None:
            evidence["shooter_approach"] = shooter_evidence
        if stair_evidence is not None:
            evidence["stair_escape"] = stair_evidence
        if withdrawal_evidence is not None:
            evidence["moving_withdrawal"] = withdrawal_evidence
        if chosen.action.objective == "prepare clear departure":
            evidence["stair_reevaluation"] = stair_preparation_evidence
        self.previous = obs
        self.last_action = replace(chosen.action, assessment=evidence)
        return self.last_action

    def routine(self, obs, action):
        self.observe(obs)
        self.previous = obs
        if (not action.assessment and self.latest_assessment is not None
                and self._assessment_observation is obs):
            action = replace(action, assessment={
                "category": self.latest_assessment.category,
                "assessment": asdict(self.latest_assessment),
                "policy": asdict(self.config),
                "candidates": [{"kind": action.kind, "value": action.value}],
                "chosen": {"kind": action.kind, "value": action.value},
            })
        self.last_action = action
        return action

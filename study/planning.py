"""Unevaluated visible-evidence dependency guidance; never executes an action.

Only proposed arguments and recorded native/user observations are inputs. No
environment, task catalogue, reference, scorer, provider or budget writer access.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json

from .intents import canonical_json

DEVELOPMENT_VERSION='current-dev-v5'
VERSION='visible-evidence-planning-current-v5'
MODEL_EVALUATED=False


def _finite_money(value):
    if type(value) not in (int,float,str): return False
    try: return Decimal(str(value)).is_finite()
    except InvalidOperation: return False


@dataclass(frozen=True)
class PlanningPolicy:
    stall_after_identical_reads: int = 3

    def __post_init__(self):
        if type(self.stall_after_identical_reads) is not int or self.stall_after_identical_reads<3:
            raise ValueError('Read-loop stop requires at least three identical consecutive observations')


@dataclass(frozen=True)
class DependencyPlan:
    _json: str

    def to_dict(self): return json.loads(self._json)

    @property
    def feasible(self): return self.to_dict()['budget_feasible_lower_bound']

    def feedback(self):
        p=self.to_dict()
        visible=', '.join(p['already_visible_fields']) or 'none yet'
        lines=[f"Missing material details: {', '.join(p['missing_fields'])}. Already visible: {visible}."]
        for index,suggestion in enumerate(p['suggestions'],1):
            lines.append(f"Suggested next read/clarification {index}: {canonical_json(suggestion['action'])}. {suggestion['reason']}")
        if p['unresolved_dependencies']:
            lines.append('Still unresolved: '+'; '.join(p['unresolved_dependencies'])+'. Do not guess missing values.')
        lines.append(f"Budget lower bound: {p['minimum_native_actions']} native actions and {p['minimum_actor_turns']} actor turns (including a new proposal, fresh confirmation and one write); remaining {p['remaining_native_actions']} native / {p['remaining_actor_turns']} actor.")
        if not p['budget_feasible_lower_bound']:
            lines.append(f"Insufficient budget: short by {p['native_action_shortfall']} native actions / {p['actor_turn_shortfall']} actor turns. No read, confirmation or mutation was dispatched by this plan.")
        else:
            lines.append('This is a lower bound, not a completion guarantee. Collect evidence, then propose the exact operation again; only fresh customer consent can authorize its dispatch.')
        return '\n'.join(lines)


def plan_dependencies(store,action,missing_fields,*,remaining_native_actions,remaining_actor_turns):
    if (type(remaining_native_actions) is not int or type(remaining_actor_turns) is not int or
            remaining_native_actions<0 or remaining_actor_turns<0):
        raise ValueError('Nonnegative remaining action and actor budgets required')
    name,args=action['name'],action['arguments']
    store.registry.validate_arguments(store.domain,name,args)
    spec=store.registry.get(store.domain,name)
    if spec.effect!='mutation' or any(field not in spec.display_fields for field in missing_fields):
        raise ValueError('Dependency guidance requires registered missing material fields')
    facts=store.facts_for(name,args)
    suggestions=[]; unresolved=[]; deduplicated=set()

    def suggest(tool,arguments,reason,refs=()):
        candidate={'name':tool,'arguments':arguments}
        if tool!='respond':
            if store.registry.get(store.domain,tool).effect!='read': raise ValueError('Planner cannot suggest mutations')
            store.registry.validate_arguments(store.domain,tool,arguments)
        identity=canonical_json(candidate)
        if identity not in deduplicated:
            suggestions.append({'action':candidate,'reason':reason,'visible_evidence':[ref.to_dict() for ref in refs]})
            deduplicated.add(identity)

    key='order_id' if store.domain=='retail' else 'reservation_id'
    entity=store._entity(key,args[key]) if key in args else None
    if key in args and entity is None:
        tool='get_order_details' if store.domain=='retail' else 'get_reservation_details'
        suggest(tool,{key:args[key]},'Read the proposed object; no valid latest related object is visible. The target ID is already in the proposal.')
        unresolved.append('Other dependencies must be recomputed from that actual response; unknown routes, prices and passengers are not invented')
    elif entity is not None and store.domain=='airline' and name=='update_reservation_flights' and 'price_difference' in missing_fields:
        value=entity.value
        flights=value.get('flights')
        if type(flights) is not list or type(value.get('passengers')) is not list or not value['passengers']:
            suggest('get_reservation_details',{'reservation_id':args['reservation_id']},'Latest reservation lacks valid flight/passenger inputs.',[store._ref(entity)])
        else:
            for proposed in args['flights']:
                old_index=next((i for i,row in enumerate(flights) if type(row) is dict and
                    row.get('flight_number')==proposed['flight_number'] and row.get('date')==proposed['date']),None)
                if value.get('cabin')==args['cabin'] and old_index is not None:
                    # The unchanged extractor decides whether original monetary
                    # values are usable; malformed values remain unresolved.
                    if _finite_money(flights[old_index].get('price')): continue
                if store._searched_flight(proposed['flight_number'],proposed['date'],args['cabin']) is not None: continue
                if old_index is None:
                    unresolved.append(f"No visible route for proposed flight {proposed['flight_number']} on {proposed['date']}; obtain an ordinary route observation or revise the proposal")
                    continue
                old=flights[old_index]
                if any(type(old.get(field)) is not str or not old[field] for field in ('origin','destination')):
                    unresolved.append(f"Visible route fields are missing for proposed flight {proposed['flight_number']}; re-read the reservation")
                    suggest('get_reservation_details',{'reservation_id':args['reservation_id']},'Refresh incomplete visible route fields.',[store._ref(entity)])
                    continue
                suggest('search_direct_flight',{'origin':old['origin'],'destination':old['destination'],'date':proposed['date']},
                    f"Need latest observed {args['cabin']} price for flight {proposed['flight_number']}; the route/date come from this related visible reservation row, not hidden inventory.",
                    [store._ref(entity,f'/flights/{old_index}')])
            if not suggestions and store._formula_inputs(name,args) is None:
                unresolved.append('Latest related monetary inputs are invalid or incomplete; a quoted actor calculation cannot substitute for native prices')
    elif entity is not None and store.domain=='retail' and name in {'modify_pending_order_items','exchange_delivered_order_items'} and 'price_difference' in missing_fields:
        items=entity.value.get('items',[])
        for old_id,new_id in zip(args['item_ids'],args['new_item_ids']):
            index=next((i for i,item in enumerate(items) if type(item) is dict and item.get('item_id')==old_id),None)
            if index is None or type(items[index].get('product_id')) is not str:
                unresolved.append(f"Proposed original item {old_id} has no visible related product ID; re-read the order or revise the proposal")
                continue
            product=store._product(items[index]['product_id'])
            variants=product.value.get('variants',{}) if product else {}
            if type(variants) is dict and type(variants.get(new_id)) is dict and _finite_money(variants[new_id].get('price')):
                continue
            suggest('get_product_details',{'product_id':items[index]['product_id']},
                f"Read actual variant prices for proposed replacement {new_id}; product ID comes from the related visible order item.",
                [store._ref(entity,f'/items/{index}')])
        if not suggestions:
            unresolved.append('Related item/variant monetary inputs remain invalid; do not fill them from an actor estimate')
    elif entity is not None and store.domain=='airline' and name=='cancel_reservation' and 'cancellation_reason' in missing_fields:
        suggest('respond',{'content':'Please state the cancellation reason: change of plan, airline cancelled flight, or other reasons.'},
            'An actual customer reason is missing; this clarification is not consent to cancel.',[store._ref(entity)])
    else:
        unresolved.extend(f"No safe exact next read is derivable for {field}; obtain related visible evidence or customer clarification" for field in missing_fields)
    minimum_native=len(suggestions)+2
    minimum_actor=len(suggestions)+1
    payload={'version':VERSION,'development_version':DEVELOPMENT_VERSION,'model_evaluated':False,
        'missing_fields':list(missing_fields),'already_visible_fields':[fact.name for fact in facts],
        'already_visible_evidence':[fact.evidence.to_dict() for fact in facts],
        'suggestions':suggestions,'unresolved_dependencies':unresolved,
        'minimum_native_actions':minimum_native,'minimum_actor_turns':minimum_actor,
        'remaining_native_actions':remaining_native_actions,'remaining_actor_turns':remaining_actor_turns,
        'native_action_shortfall':max(0,minimum_native-remaining_native_actions),
        'actor_turn_shortfall':max(0,minimum_actor-remaining_actor_turns),
        'budget_feasible_lower_bound':minimum_native<=remaining_native_actions and minimum_actor<=remaining_actor_turns,
        'dispatches_performed':0,'authority_to_dispatch':False}
    return DependencyPlan(canonical_json(payload))

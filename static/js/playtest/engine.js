/**
 * Client-side Playtest Game Engine
 * Pure functional state reducer, combat calculator, and autosave coordinator.
 */

(function (root, factory) {
    if (typeof define === 'function' && define.amd) {
        define([], factory);
    } else if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.PlaytestEngine = factory();
    }
}(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    /**
     * Deep clone helper.
     */
    function clone(obj) {
        if (!obj) return obj;
        return JSON.parse(JSON.stringify(obj));
    }

    /**
     * Formats an ISO timestamp for game log.
     */
    function nowIso() {
        return new Date().toISOString();
    }

    /**
     * Appends a log entry (capped at 300 entries).
     */
    function appendLog(state, seat, msg) {
        if (!state.log) state.log = [];
        state.log.push({
            t: nowIso(),
            seat: seat,
            msg: msg
        });
        if (state.log.length > 300) {
            state.log.shift();
        }
    }

    /**
     * Gets effective power and toughness for a card instance.
     */
    function getEffectivePT(state, instId) {
        var card = state.cards[instId];
        if (!card) return { power: 0, toughness: 0, hasPT: false };
        var ref = state.card_refs[card.card_ref] || {};

        var baseP = parseInt(ref.power, 10);
        var baseT = parseInt(ref.toughness, 10);
        if (isNaN(baseP) || isNaN(baseT)) {
            return { power: 0, toughness: 0, hasPT: false };
        }

        var pMod = (card.pt_mod && card.pt_mod[0]) || 0;
        var tMod = (card.pt_mod && card.pt_mod[1]) || 0;

        var p1p1 = (card.counters && card.counters['p1p1']) || 0;
        var m1m1 = (card.counters && card.counters['m1m1']) || 0;

        var finalP = baseP + pMod + p1p1 - m1m1;
        var finalT = baseT + tMod + p1p1 - m1m1;

        return {
            power: finalP,
            toughness: finalT,
            hasPT: true,
            basePower: baseP,
            baseToughness: baseT
        };
    }

    /**
     * Checks if a card has a given keyword (case-insensitive).
     */
    function hasKeyword(state, instId, kw) {
        var card = state.cards[instId];
        if (!card) return false;
        var ref = state.card_refs[card.card_ref] || {};
        var kws = ref.keywords || [];
        var target = kw.toLowerCase();
        for (var i = 0; i < kws.length; i++) {
            if (kws[i].toLowerCase() === target) return true;
        }
        return false;
    }

    /**
     * Pure action reducer: (state, action) -> new_state
     */
    function reduce(prevState, action) {
        var state = clone(prevState);
        var type = action.type;
        var payload = action.payload || {};

        switch (type) {
            case 'DRAW_CARD': {
                var seatIdx = payload.seat !== undefined ? payload.seat : state.viewing_seat;
                var count = payload.count || 1;
                var seat = state.seats[seatIdx];
                if (!seat) return state;

                var drawnNames = [];
                for (var i = 0; i < count; i++) {
                    if (seat.zones.library.length === 0) break;
                    var instId = seat.zones.library.shift();
                    seat.zones.hand.push(instId);
                    var ref = state.card_refs[state.cards[instId].card_ref];
                    if (ref) drawnNames.push(ref.name);
                }

                if (drawnNames.length > 0) {
                    var msg = count === 1
                        ? seat.name + ' drew ' + drawnNames[0]
                        : seat.name + ' drew ' + drawnNames.length + ' cards';
                    appendLog(state, seatIdx, msg);
                }
                return state;
            }

            case 'PLAY_CARD': {
                var seatIdx = payload.seat !== undefined ? payload.seat : state.viewing_seat;
                var instId = payload.instance_id;
                var tapped = !!payload.tapped;
                var seat = state.seats[seatIdx];
                var card = state.cards[instId];
                if (!seat || !card) return state;

                var handIdx = seat.zones.hand.indexOf(instId);
                if (handIdx === -1) return state;

                seat.zones.hand.splice(handIdx, 1);
                var ref = state.card_refs[card.card_ref] || {};
                var typeLine = (ref.type_line || '').toLowerCase();

                // Smart routing
                if (typeLine.includes('instant') || typeLine.includes('sorcery')) {
                    // Instants & sorceries go straight to graveyard upon resolution
                    seat.zones.graveyard.push(instId);
                    appendLog(state, seatIdx, seat.name + ' cast ' + ref.name + ' (to Graveyard)');
                } else {
                    // Permanents go to battlefield
                    seat.zones.battlefield.push(instId);
                    card.tapped = tapped;
                    card.summoning_sick = typeLine.includes('creature') && !hasKeyword(state, instId, 'Haste');
                    appendLog(state, seatIdx, seat.name + ' played ' + ref.name + (tapped ? ' (tapped)' : ''));
                }
                return state;
            }

            case 'CAST_COMMANDER': {
                var seatIdx = payload.seat !== undefined ? payload.seat : state.viewing_seat;
                var instId = payload.instance_id;
                var seat = state.seats[seatIdx];
                var card = state.cards[instId];
                if (!seat || !card) return state;

                var cmdIdx = seat.zones.command.indexOf(instId);
                if (cmdIdx === -1) return state;

                seat.zones.command.splice(cmdIdx, 1);
                seat.zones.battlefield.push(instId);
                card.tapped = !!payload.tapped;

                // Increment commander cast count
                if (!seat.commander_casts) seat.commander_casts = {};
                seat.commander_casts[instId] = (seat.commander_casts[instId] || 0) + 1;

                var ref = state.card_refs[card.card_ref] || {};
                var casts = seat.commander_casts[instId];
                var tax = (casts - 1) * 2;
                appendLog(state, seatIdx, seat.name + ' cast Commander ' + ref.name + ' (Cast #' + casts + ', Tax was +' + tax + ')');
                return state;
            }

            case 'TAP_PERMANENT': {
                var instId = payload.instance_id;
                var card = state.cards[instId];
                if (!card) return state;
                var newState = payload.state !== undefined ? !!payload.state : !card.tapped;
                card.tapped = newState;
                return state;
            }

            case 'UNTAP_ALL': {
                var seatIdx = payload.seat !== undefined ? payload.seat : state.viewing_seat;
                var seat = state.seats[seatIdx];
                if (!seat) return state;

                for (var i = 0; i < seat.zones.battlefield.length; i++) {
                    var inst = state.cards[seat.zones.battlefield[i]];
                    if (inst) inst.tapped = false;
                }
                appendLog(state, seatIdx, seat.name + ' untapped all permanents');
                return state;
            }

            case 'MOVE_CARD': {
                var instId = payload.instance_id;
                var fromSeatIdx = payload.from_seat !== undefined ? payload.from_seat : state.viewing_seat;
                var toSeatIdx = payload.to_seat !== undefined ? payload.to_seat : fromSeatIdx;
                var fromZone = payload.from_zone;
                var toZone = payload.to_zone;
                var position = payload.position || 'bottom'; // 'top' or 'bottom'
                var card = state.cards[instId];

                var fromSeat = state.seats[fromSeatIdx];
                var toSeat = state.seats[toSeatIdx];
                if (!fromSeat || !toSeat || !card) return state;

                // Remove from fromZone
                var fZoneList = fromSeat.zones[fromZone];
                if (fZoneList) {
                    var idx = fZoneList.indexOf(instId);
                    if (idx !== -1) fZoneList.splice(idx, 1);
                }

                // If moving a token off battlefield, destroy token
                if (card.token && toZone !== 'battlefield') {
                    delete state.cards[instId];
                    appendLog(state, fromSeatIdx, 'Token ceased to exist.');
                    return state;
                }

                // Reset state when moving out of battlefield
                if (fromZone === 'battlefield' && toZone !== 'battlefield') {
                    card.tapped = false;
                    card.pt_mod = [0, 0];
                    card.counters = {};
                }

                // Add to toZone
                var tZoneList = toSeat.zones[toZone];
                if (tZoneList) {
                    if (position === 'top') {
                        tZoneList.unshift(instId);
                    } else {
                        tZoneList.push(instId);
                    }
                }

                var ref = state.card_refs[card.card_ref] || {};
                appendLog(state, toSeatIdx, 'Moved ' + ref.name + ' from ' + fromZone + ' to ' + toZone);
                return state;
            }

            case 'ADJUST_LIFE': {
                var seatIdx = payload.seat !== undefined ? payload.seat : state.viewing_seat;
                var delta = payload.delta || 0;
                var seat = state.seats[seatIdx];
                if (!seat) return state;

                seat.life += delta;
                appendLog(state, seatIdx, seat.name + ' life adjusted (' + (delta > 0 ? '+' : '') + delta + ') to ' + seat.life);
                return state;
            }

            case 'ADJUST_POISON': {
                var seatIdx = payload.seat !== undefined ? payload.seat : state.viewing_seat;
                var delta = payload.delta || 0;
                var seat = state.seats[seatIdx];
                if (!seat) return state;

                seat.poison = Math.max(0, seat.poison + delta);
                appendLog(state, seatIdx, seat.name + ' poison adjusted to ' + seat.poison);
                return state;
            }

            case 'ADJUST_COMMANDER_DAMAGE': {
                var targetSeatIdx = payload.target_seat;
                var sourceSeatIdx = payload.source_seat;
                var delta = payload.delta || 0;
                var seat = state.seats[targetSeatIdx];
                if (!seat) return state;

                if (!seat.commander_damage) seat.commander_damage = {};
                var curr = seat.commander_damage[String(sourceSeatIdx)] || 0;
                var updated = Math.max(0, curr + delta);
                seat.commander_damage[String(sourceSeatIdx)] = updated;

                var srcSeat = state.seats[sourceSeatIdx];
                var srcName = srcSeat ? srcSeat.name : 'Player ' + (sourceSeatIdx + 1);
                appendLog(state, targetSeatIdx, seat.name + ' took commander damage from ' + srcName + ' (' + updated + '/21)');
                return state;
            }

            case 'ADJUST_COUNTERS': {
                var instId = payload.instance_id;
                var counterType = payload.counter_type || 'p1p1';
                var delta = payload.delta || 0;
                var card = state.cards[instId];
                if (!card) return state;

                if (!card.counters) card.counters = {};
                var val = (card.counters[counterType] || 0) + delta;
                if (val <= 0) {
                    delete card.counters[counterType];
                } else {
                    card.counters[counterType] = val;
                }
                return state;
            }

            case 'ADJUST_PT_MOD': {
                var instId = payload.instance_id;
                var dP = payload.delta_power || 0;
                var dT = payload.delta_toughness || 0;
                var card = state.cards[instId];
                if (!card) return state;

                if (!card.pt_mod) card.pt_mod = [0, 0];
                card.pt_mod[0] += dP;
                card.pt_mod[1] += dT;
                return state;
            }

            case 'TRANSFORM_DFC': {
                var instId = payload.instance_id;
                var card = state.cards[instId];
                if (!card) return state;
                card.face = card.face === 0 ? 1 : 0;
                var ref = state.card_refs[card.card_ref] || {};
                appendLog(state, state.viewing_seat, 'Transformed ' + ref.name);
                return state;
            }

            case 'SPAWN_TOKEN': {
                var seatIdx = payload.seat !== undefined ? payload.seat : state.viewing_seat;
                var count = payload.count || 1;
                var name = payload.name || 'Soldier';
                var power = payload.power !== undefined ? String(payload.power) : '1';
                var toughness = payload.toughness !== undefined ? String(payload.toughness) : '1';
                var typeLine = payload.type_line || 'Creature — Token';
                var keywords = payload.keywords || [];
                var seat = state.seats[seatIdx];
                if (!seat) return state;

                var refKey = 'token_' + name.toLowerCase().replace(/\s+/g, '_');
                if (!state.card_refs[refKey]) {
                    state.card_refs[refKey] = {
                        name: name + ' Token',
                        type_line: typeLine,
                        mana_cost: '',
                        cmc: 0,
                        power: power,
                        toughness: toughness,
                        keywords: keywords,
                        image_uri: '',
                        small_image_uri: '',
                        art_crop_uri: '',
                        oracle_text: keywords.join(', '),
                        produced_mana: typeLine.includes('Treasure') ? ['W', 'U', 'B', 'R', 'G', 'C'] : [],
                        card_faces: [],
                        rarity: 'common',
                        colors: []
                    };
                }

                for (var i = 0; i < count; i++) {
                    var instId = 'inst_token_' + seatIdx + '_' + Math.random().toString(36).substring(2, 9);
                    state.cards[instId] = {
                        card_ref: refKey,
                        tapped: false,
                        counters: {},
                        pt_mod: [0, 0],
                        face: 0,
                        token: true,
                        summoning_sick: typeLine.includes('Creature') && !keywords.includes('Haste'),
                        is_commander: false
                    };
                    seat.zones.battlefield.push(instId);
                }

                appendLog(state, seatIdx, seat.name + ' created ' + (count > 1 ? count + 'x ' : '') + name + ' Token');
                return state;
            }

            case 'MULLIGAN_TAKE': {
                var seatIdx = payload.seat !== undefined ? payload.seat : state.viewing_seat;
                var seat = state.seats[seatIdx];
                if (!seat) return state;

                // Return hand to library and shuffle
                while (seat.zones.hand.length > 0) {
                    seat.zones.library.push(seat.zones.hand.pop());
                }

                // Simple in-place shuffle
                for (var i = seat.zones.library.length - 1; i > 0; i--) {
                    var j = Math.floor(Math.random() * (i + 1));
                    var tmp = seat.zones.library[i];
                    seat.zones.library[i] = seat.zones.library[j];
                    seat.zones.library[j] = tmp;
                }

                // Draw 7 cards
                for (var k = 0; k < 7; k++) {
                    if (seat.zones.library.length > 0) {
                        seat.zones.hand.push(seat.zones.library.shift());
                    }
                }

                if (!seat.mulligan) seat.mulligan = { count: 0, kept: false, hand_size: 7 };
                seat.mulligan.count += 1;
                // Free first mulligan in Commander pods: cards to bottom = max(0, count - 1)
                var cardsToBottom = Math.max(0, seat.mulligan.count - 1);
                seat.mulligan.hand_size = 7 - cardsToBottom;

                appendLog(state, seatIdx, seat.name + ' took Mulligan #' + seat.mulligan.count + ' (Put ' + cardsToBottom + ' to bottom)');
                return state;
            }

            case 'MULLIGAN_KEEP': {
                var seatIdx = payload.seat !== undefined ? payload.seat : state.viewing_seat;
                var seat = state.seats[seatIdx];
                if (!seat) return state;
                if (!seat.mulligan) seat.mulligan = { count: 0, kept: true, hand_size: 7 };
                seat.mulligan.kept = true;
                appendLog(state, seatIdx, seat.name + ' kept opening hand.');
                return state;
            }

            case 'PASS_TURN': {
                var currSeat = state.active_seat;
                var nextSeat = (currSeat + 1) % state.seats.length;

                // If next seat wraps back to seat 0, increment turn count
                if (nextSeat === 0) {
                    state.turn += 1;
                }

                state.active_seat = nextSeat;
                state.phase = 'main1';

                if (state.settings.follow_active_player) {
                    state.viewing_seat = nextSeat;
                    // Reset hand revealed flag on seat change if privacy setting is enabled
                    state.hand_revealed = !state.settings.hide_hands;
                }

                var nextPlayer = state.seats[nextSeat];
                if (nextPlayer) {
                    // Clear summoning sickness on next player's creatures
                    for (var i = 0; i < nextPlayer.zones.battlefield.length; i++) {
                        var card = state.cards[nextPlayer.zones.battlefield[i]];
                        if (card) card.summoning_sick = false;
                    }

                    // Auto untap and draw if enabled
                    if (state.settings.auto_untap_draw) {
                        for (var j = 0; j < nextPlayer.zones.battlefield.length; j++) {
                            var bCard = state.cards[nextPlayer.zones.battlefield[j]];
                            if (bCard) bCard.tapped = false;
                        }
                        // Rule 103.8: Player 1 skips draw on Turn 1 in 1v1 duel only
                        var skipDraw = (state.turn === 1 && nextSeat === 0 && state.seats.length <= 2);
                        if (!skipDraw && nextPlayer.zones.library.length > 0) {
                            var drawnId = nextPlayer.zones.library.shift();
                            nextPlayer.zones.hand.push(drawnId);
                        }
                    }
                }

                appendLog(state, nextSeat, 'Turn ' + state.turn + ' begun (' + (nextPlayer ? nextPlayer.name : 'Seat ' + nextSeat) + ')');
                return state;
            }

            case 'SWITCH_SEAT': {
                var targetSeat = payload.seat;
                if (targetSeat >= 0 && targetSeat < state.seats.length) {
                    state.viewing_seat = targetSeat;
                    // If hide_hands is true, require tap to reveal
                    state.hand_revealed = !state.settings.hide_hands;
                }
                return state;
            }

            case 'REVEAL_HAND': {
                state.hand_revealed = true;
                return state;
            }

            case 'SET_PHASE': {
                state.phase = payload.phase || 'main1';
                return state;
            }

            case 'UPDATE_SETTING': {
                var key = payload.key;
                var val = payload.value;
                if (!state.settings) state.settings = {};
                state.settings[key] = val;
                return state;
            }

            // =================================================================
            // Guided Step-by-Step Combat Reducer
            // =================================================================

            case 'COMBAT_START': {
                state.phase = 'combat';
                state.combat = {
                    step: 'attackers', // 'attackers' | 'blockers' | 'tricks' | 'preview'
                    attacking_seat: state.active_seat,
                    attackers: {}, // instId -> { targetSeat: 1, originalTapped: bool }
                    blockers: {},  // instId (blocker) -> instId (attacker)
                    preview: null
                };
                appendLog(state, state.active_seat, 'Entered Combat Phase.');
                return state;
            }

            case 'COMBAT_TOGGLE_ATTACKER': {
                if (!state.combat || state.combat.step !== 'attackers') return state;
                var instId = payload.instance_id;
                var card = state.cards[instId];
                if (!card) return state;

                if (state.combat.attackers[instId]) {
                    // Untoggle
                    delete state.combat.attackers[instId];
                } else {
                    // Default target: next seat in turn order
                    var defTarget = (state.active_seat + 1) % state.seats.length;
                    state.combat.attackers[instId] = {
                        target_seat: payload.target_seat !== undefined ? payload.target_seat : defTarget,
                        original_tapped: card.tapped
                    };
                }
                return state;
            }

            case 'COMBAT_SET_ATTACKER_TARGET': {
                if (!state.combat || !state.combat.attackers) return state;
                var instId = payload.instance_id;
                var targetSeat = payload.target_seat;
                if (state.combat.attackers[instId]) {
                    state.combat.attackers[instId].target_seat = targetSeat;
                }
                return state;
            }

            case 'COMBAT_ATTACK_ALL': {
                if (!state.combat || state.combat.step !== 'attackers') return state;
                var seat = state.seats[state.active_seat];
                if (!seat) return state;
                var defTarget = (state.active_seat + 1) % state.seats.length;

                for (var i = 0; i < seat.zones.battlefield.length; i++) {
                    var instId = seat.zones.battlefield[i];
                    var card = state.cards[instId];
                    var ref = state.card_refs[card.card_ref] || {};
                    var isCreature = (ref.type_line || '').toLowerCase().includes('creature');
                    var pt = getEffectivePT(state, instId);

                    if (isCreature && pt.hasPT && !card.tapped && (!card.summoning_sick || hasKeyword(state, instId, 'Haste'))) {
                        if (!hasKeyword(state, instId, 'Defender')) {
                            state.combat.attackers[instId] = {
                                target_seat: defTarget,
                                original_tapped: card.tapped
                            };
                        }
                    }
                }
                return state;
            }

            case 'COMBAT_CONFIRM_ATTACKERS': {
                if (!state.combat) return state;
                var attackerIds = Object.keys(state.combat.attackers);
                if (attackerIds.length === 0) {
                    // No attackers, skip combat
                    state.combat = null;
                    state.phase = 'main2';
                    appendLog(state, state.active_seat, 'Combat skipped (no attackers declared).');
                    return state;
                }

                // Tap attackers unless they have Vigilance
                for (var i = 0; i < attackerIds.length; i++) {
                    var aId = attackerIds[i];
                    var aCard = state.cards[aId];
                    if (aCard && !hasKeyword(state, aId, 'Vigilance')) {
                        aCard.tapped = true;
                    }
                }

                // If solo goldfish (only 1 seat), jump directly to preview
                if (state.seats.length === 1) {
                    state.combat.step = 'preview';
                    state.combat.preview = calculateCombatDamage(state);
                } else {
                    state.combat.step = 'blockers';
                }
                return state;
            }

            case 'COMBAT_ASSIGN_BLOCKER': {
                if (!state.combat || state.combat.step !== 'blockers') return state;
                var blockerId = payload.blocker_id;
                var attackerId = payload.attacker_id;
                if (state.combat.blockers[blockerId] === attackerId) {
                    delete state.combat.blockers[blockerId];
                } else {
                    state.combat.blockers[blockerId] = attackerId;
                }
                return state;
            }

            case 'COMBAT_CONFIRM_BLOCKERS': {
                if (!state.combat) return state;
                state.combat.step = 'tricks';
                return state;
            }

            case 'COMBAT_CONFIRM_TRICKS': {
                if (!state.combat) return state;
                state.combat.step = 'preview';
                state.combat.preview = calculateCombatDamage(state);
                return state;
            }

            case 'COMBAT_UPDATE_PREVIEW': {
                if (!state.combat || !state.combat.preview) return state;
                // Allow manual overrides to the preview numbers
                if (payload.preview) {
                    state.combat.preview = payload.preview;
                }
                return state;
            }

            case 'COMBAT_APPLY': {
                if (!state.combat || !state.combat.preview) return state;
                var preview = state.combat.preview;

                // 1. Apply life adjustments
                if (preview.life_deltas) {
                    for (var sIdx in preview.life_deltas) {
                        var delta = preview.life_deltas[sIdx];
                        if (state.seats[sIdx]) {
                            state.seats[sIdx].life += delta;
                        }
                    }
                }

                // 2. Apply poison adjustments
                if (preview.poison_deltas) {
                    for (var pIdx in preview.poison_deltas) {
                        var pDelta = preview.poison_deltas[pIdx];
                        if (state.seats[pIdx]) {
                            state.seats[pIdx].poison += pDelta;
                        }
                    }
                }

                // 3. Apply commander damage
                if (preview.commander_damage_deltas) {
                    for (var i = 0; i < preview.commander_damage_deltas.length; i++) {
                        var cd = preview.commander_damage_deltas[i];
                        var tgtSeat = state.seats[cd.target_seat];
                        if (tgtSeat) {
                            if (!tgtSeat.commander_damage) tgtSeat.commander_damage = {};
                            var curCD = tgtSeat.commander_damage[String(cd.source_seat)] || 0;
                            tgtSeat.commander_damage[String(cd.source_seat)] = curCD + cd.damage;
                        }
                    }
                }

                // 4. Move dead creatures to Graveyard / Command Zone
                if (preview.dead_instances && preview.dead_instances.length > 0) {
                    for (var k = 0; k < preview.dead_instances.length; k++) {
                        var deadId = preview.dead_instances[k];
                        var deadCard = state.cards[deadId];
                        if (!deadCard) continue;

                        // Find which seat controls it
                        for (var s = 0; s < state.seats.length; s++) {
                            var bList = state.seats[s].zones.battlefield;
                            var bIdx = bList.indexOf(deadId);
                            if (bIdx !== -1) {
                                bList.splice(bIdx, 1);
                                if (deadCard.token) {
                                    delete state.cards[deadId];
                                } else if (deadCard.is_commander) {
                                    // Commanders return to Command Zone
                                    state.seats[s].zones.command.push(deadId);
                                    deadCard.tapped = false;
                                } else {
                                    state.seats[s].zones.graveyard.push(deadId);
                                    deadCard.tapped = false;
                                }
                                break;
                            }
                        }
                    }
                }

                // Conclude combat and proceed to Main 2
                state.combat = null;
                state.phase = 'main2';
                appendLog(state, state.active_seat, 'Combat applied: ' + (preview.summary || 'Damage dealt'));
                return state;
            }

            case 'COMBAT_CANCEL': {
                if (!state.combat) return state;
                // Revert any tapped status made for attackers
                var atkIds = Object.keys(state.combat.attackers || {});
                for (var i = 0; i < atkIds.length; i++) {
                    var aCard = state.cards[atkIds[i]];
                    var orig = state.combat.attackers[atkIds[i]].original_tapped;
                    if (aCard && orig !== undefined) {
                        aCard.tapped = orig;
                    }
                }
                state.combat = null;
                state.phase = 'main1';
                appendLog(state, state.active_seat, 'Combat cancelled.');
                return state;
            }

            default:
                return state;
        }
    }

    /**
     * Calculates combat damage incorporating MTG keywords:
     * Trample, Deathtouch, Lifelink, Infect/Toxic, First/Double strike, Commander damage.
     */
    function calculateCombatDamage(state) {
        var combat = state.combat;
        if (!combat || !combat.attackers) return null;

        var attackingSeat = combat.attacking_seat;
        var lifeDeltas = {};
        var poisonDeltas = {};
        var commanderDamageDeltas = [];
        var deadInstances = [];
        var summaryLines = [];

        var attackerIds = Object.keys(combat.attackers);

        for (var i = 0; i < attackerIds.length; i++) {
            var atkId = attackerIds[i];
            var atkCard = state.cards[atkId];
            var atkRef = state.card_refs[atkCard.card_ref] || {};
            var atkPT = getEffectivePT(state, atkId);
            var atkPower = Math.max(0, atkPT.power);
            var targetSeat = combat.attackers[atkId].target_seat;

            var hasTrample = hasKeyword(state, atkId, 'Trample');
            var hasDeathtouch = hasKeyword(state, atkId, 'Deathtouch');
            var hasLifelink = hasKeyword(state, atkId, 'Lifelink');
            var hasInfect = hasKeyword(state, atkId, 'Infect');
            var isDoubleStrike = hasKeyword(state, atkId, 'Double strike');
            var strikeMultiplier = isDoubleStrike ? 2 : 1;

            // Find all blockers assigned to this attacker
            var assignedBlockerIds = [];
            for (var bId in combat.blockers) {
                if (combat.blockers[bId] === atkId) {
                    assignedBlockerIds.push(bId);
                }
            }

            var totalAttackerDamage = atkPower * strikeMultiplier;

            if (assignedBlockerIds.length === 0) {
                // UNBLOCKED
                var unblockedDamage = totalAttackerDamage;

                if (unblockedDamage > 0) {
                    if (hasInfect) {
                        poisonDeltas[targetSeat] = (poisonDeltas[targetSeat] || 0) + unblockedDamage;
                        summaryLines.push(atkRef.name + ' dealt ' + unblockedDamage + ' poison to Player ' + (targetSeat + 1));
                    } else {
                        lifeDeltas[targetSeat] = (lifeDeltas[targetSeat] || 0) - unblockedDamage;
                        summaryLines.push(atkRef.name + ' dealt ' + unblockedDamage + ' damage to Player ' + (targetSeat + 1));
                    }

                    if (hasLifelink) {
                        lifeDeltas[attackingSeat] = (lifeDeltas[attackingSeat] || 0) + unblockedDamage;
                    }

                    if (atkCard.is_commander) {
                        commanderDamageDeltas.push({
                            target_seat: targetSeat,
                            source_seat: attackingSeat,
                            damage: unblockedDamage
                        });
                    }
                }
            } else {
                // BLOCKED
                var remainingDamage = atkPower;
                var totalBlockerDamageToAttacker = 0;

                for (var b = 0; b < assignedBlockerIds.length; b++) {
                    var blkId = assignedBlockerIds[b];
                    var blkCard = state.cards[blkId];
                    var blkRef = state.card_refs[blkCard.card_ref] || {};
                    var blkPT = getEffectivePT(state, blkId);
                    var blkDeathtouch = hasKeyword(state, blkId, 'Deathtouch');

                    // Lethal damage needed
                    var lethalNeeded = hasDeathtouch ? 1 : Math.max(1, blkPT.toughness);
                    var damageToBlocker = Math.min(remainingDamage, lethalNeeded);
                    remainingDamage = Math.max(0, remainingDamage - damageToBlocker);

                    // Did blocker die?
                    if (damageToBlocker >= lethalNeeded || hasDeathtouch) {
                        if (deadInstances.indexOf(blkId) === -1) {
                            deadInstances.push(blkId);
                            summaryLines.push(blkRef.name + ' killed by ' + atkRef.name);
                        }
                    }

                    // Blocker deals damage back to attacker
                    var bPower = Math.max(0, blkPT.power);
                    totalBlockerDamageToAttacker += bPower;

                    if (blkDeathtouch && bPower > 0) {
                        if (deadInstances.indexOf(atkId) === -1) {
                            deadInstances.push(atkId);
                            summaryLines.push(atkRef.name + ' killed by Deathtouch from ' + blkRef.name);
                        }
                    }
                }

                // Check if attacker died from blocker damage
                if (totalBlockerDamageToAttacker >= atkPT.toughness && atkPT.toughness > 0) {
                    if (deadInstances.indexOf(atkId) === -1) {
                        deadInstances.push(atkId);
                        summaryLines.push(atkRef.name + ' killed by blockers (' + totalBlockerDamageToAttacker + ' dmg)');
                    }
                }

                // Trample damage through blockers
                if (hasTrample && remainingDamage > 0) {
                    var trampleDamage = remainingDamage * strikeMultiplier;
                    if (hasInfect) {
                        poisonDeltas[targetSeat] = (poisonDeltas[targetSeat] || 0) + trampleDamage;
                        summaryLines.push(atkRef.name + ' trampled ' + trampleDamage + ' poison to Player ' + (targetSeat + 1));
                    } else {
                        lifeDeltas[targetSeat] = (lifeDeltas[targetSeat] || 0) - trampleDamage;
                        summaryLines.push(atkRef.name + ' trampled ' + trampleDamage + ' damage to Player ' + (targetSeat + 1));
                    }

                    if (hasLifelink) {
                        lifeDeltas[attackingSeat] = (lifeDeltas[attackingSeat] || 0) + trampleDamage;
                    }

                    if (atkCard.is_commander) {
                        commanderDamageDeltas.push({
                            target_seat: targetSeat,
                            source_seat: attackingSeat,
                            damage: trampleDamage
                        });
                    }
                }
            }
        }

        return {
            life_deltas: lifeDeltas,
            poison_deltas: poisonDeltas,
            commander_damage_deltas: commanderDamageDeltas,
            dead_instances: deadInstances,
            summary: summaryLines.join(', ') || 'No damage dealt.'
        };
    }

    /**
     * Session and Autosave Coordinator
     */
    function SessionCoordinator(sessionId, initialSession) {
        this.sessionId = sessionId;
        this.session = initialSession;
        this.state = initialSession.state;
        this.stateVersion = initialSession.state_version || 1;
        this.history = [];
        this.future = [];
        this.saveTimeout = null;
        this.isSaving = false;
        this.statusCallback = null;

        // Save initial state to localStorage backup
        this.saveLocalBackup();
    }

    SessionCoordinator.prototype.onStatusChange = function (cb) {
        this.statusCallback = cb;
    };

    SessionCoordinator.prototype.notifyStatus = function (status, msg) {
        if (this.statusCallback) {
            this.statusCallback(status, msg);
        }
    };

    SessionCoordinator.prototype.dispatch = function (action) {
        // Record undo history
        this.history.push(clone(this.state));
        if (this.history.length > 50) this.history.shift();
        this.future = []; // Clear redo stack on new action

        this.state = reduce(this.state, action);
        this.saveLocalBackup();
        this.scheduleAutosave();
        return this.state;
    };

    SessionCoordinator.prototype.undo = function () {
        if (this.history.length === 0) return null;
        this.future.push(clone(this.state));
        this.state = this.history.pop();
        this.saveLocalBackup();
        this.scheduleAutosave();
        return this.state;
    };

    SessionCoordinator.prototype.redo = function () {
        if (this.future.length === 0) return null;
        this.history.push(clone(this.state));
        this.state = this.future.pop();
        this.saveLocalBackup();
        this.scheduleAutosave();
        return this.state;
    };

    SessionCoordinator.prototype.saveLocalBackup = function () {
        try {
            if (typeof localStorage !== 'undefined') {
                localStorage.setItem('chimera_playtest_state_' + this.sessionId, JSON.stringify(this.state));
            }
        } catch (e) {
            console.warn('Failed to save to localStorage backup', e);
        }
    };

    SessionCoordinator.prototype.scheduleAutosave = function () {
        var self = this;
        this.notifyStatus('saving', 'Saving changes...');
        if (this.saveTimeout) clearTimeout(this.saveTimeout);

        this.saveTimeout = setTimeout(function () {
            self.commitSave();
        }, 600);
    };

    SessionCoordinator.prototype.commitSave = function () {
        var self = this;
        if (this.isSaving) return;
        this.isSaving = true;

        fetch('/api/playtest/' + this.sessionId + '/state', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                state: this.state,
                base_version: this.stateVersion
            })
        })
        .then(function (res) {
            self.isSaving = false;
            if (res.status === 409) {
                // Conflict
                return res.json().then(function (data) {
                    self.notifyStatus('conflict', 'Updated elsewhere. Click to review.');
                    if (window.onPlaytestConflict) {
                        window.onPlaytestConflict(data);
                    }
                });
            }
            if (!res.ok) {
                throw new Error('Server returned ' + res.status);
            }
            return res.json().then(function (data) {
                self.stateVersion = data.state_version;
                self.notifyStatus('saved', 'All changes saved');
            });
        })
        .catch(function (err) {
            self.isSaving = false;
            console.warn('Autosave failed; stored locally', err);
            self.notifyStatus('offline', 'Offline (saved locally)');
        });
    };

    return {
        reduce: reduce,
        calculateCombatDamage: calculateCombatDamage,
        getEffectivePT: getEffectivePT,
        hasKeyword: hasKeyword,
        SessionCoordinator: SessionCoordinator
    };
}));

/**
 * Playtest Arena UI Controller
 * Touch-ergonomic, mobile-first battlefield renderer, gesture coordinator, and combat stepper.
 */

(function (window, document) {
    'use strict';

    var coord = null;
    var wakeLock = null;
    var toastTimeout = null;

    // Active modal or sheet
    var activeSheet = null;

    function haptic(ms) {
        try {
            if (navigator.vibrate) navigator.vibrate(ms || 12);
        } catch (e) {}
    }

    function initWakeLock() {
        if ('wakeLock' in navigator) {
            try {
                navigator.wakeLock.request('screen').then(function (lock) {
                    wakeLock = lock;
                }).catch(function () {});
            } catch (e) {}
        }
    }

    function showToast(msg, undoAction) {
        var toast = document.getElementById('playtestToast');
        var toastMsg = document.getElementById('toastMsg');
        var toastUndo = document.getElementById('toastUndo');
        if (!toast) return;

        toastMsg.textContent = msg;
        if (undoAction) {
            toastUndo.classList.remove('hidden');
            toastUndo.onclick = function () {
                haptic(15);
                coord.undo();
                renderAll();
                toast.classList.add('hidden');
            };
        } else {
            toastUndo.classList.add('hidden');
        }

        toast.classList.remove('hidden');
        if (toastTimeout) clearTimeout(toastTimeout);
        toastTimeout = setTimeout(function () {
            toast.classList.add('hidden');
        }, 4000);
    }

    function renderAll() {
        if (!coord || !coord.state) return;
        var state = coord.state;
        renderHeader(state);
        renderSeatPills(state);
        renderOpponentStrip(state);
        renderBattlefield(state);
        renderZoneBar(state);
        renderHandDock(state);
        renderCombatStepper(state);
    }

    // -------------------------------------------------------------------------
    // Top Header & Status
    // -------------------------------------------------------------------------
    function renderHeader(state) {
        var turnBadge = document.getElementById('headerTurnBadge');
        var phaseBadge = document.getElementById('headerPhaseBadge');
        if (turnBadge) turnBadge.textContent = 'Turn ' + state.turn;
        if (phaseBadge) phaseBadge.textContent = (state.phase || 'main1').toUpperCase();

        var saveStatus = document.getElementById('saveStatusIndicator');
        if (saveStatus && !saveStatus.dataset.custom) {
            saveStatus.textContent = 'Saved';
            saveStatus.className = 'text-[10px] text-emerald-400 font-mono flex items-center gap-1';
        }
    }

    // -------------------------------------------------------------------------
    // Seat Selector Pills
    // -------------------------------------------------------------------------
    function renderSeatPills(state) {
        var container = document.getElementById('seatPillsContainer');
        if (!container) return;
        container.innerHTML = '';

        for (var i = 0; i < state.seats.length; i++) {
            var seat = state.seats[i];
            var isViewing = (i === state.viewing_seat);
            var isActiveTurn = (i === state.active_seat);

            var pill = document.createElement('button');
            pill.type = 'button';
            pill.className = 'flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 border text-xs font-mono font-bold transition ' +
                (isViewing
                    ? 'bg-cyan-500/20 border-cyan-400 text-cyan-300 shadow-[0_0_12px_rgba(6,182,212,0.3)]'
                    : 'bg-[#18202F] border-[#2A374A] text-slate-400 hover:text-white hover:border-slate-500');

            var artHtml = seat.commander_art
                ? '<img src="' + seat.commander_art + '" class="w-4 h-4 rounded-full object-cover border border-slate-600">'
                : '<div class="w-4 h-4 rounded-full bg-cyan-900 text-[9px] flex items-center justify-center font-bold text-cyan-300">P' + (i + 1) + '</div>';

            var turnDot = isActiveTurn
                ? '<span class="w-2 h-2 rounded-full bg-amber-400 animate-pulse" title="Active Turn"></span>'
                : '';

            pill.innerHTML = artHtml +
                '<span class="truncate max-w-[90px] sm:max-w-[120px]">' + seat.name + '</span>' +
                '<span class="px-1.5 py-0.2 rounded bg-slate-900 text-[10px] text-emerald-400 font-bold">' + seat.life + '</span>' +
                turnDot;

            (function (seatIndex) {
                pill.onclick = function () {
                    haptic(15);
                    coord.dispatch({ type: 'SWITCH_SEAT', payload: { seat: seatIndex } });
                    renderAll();
                };
            })(i);

            container.appendChild(pill);
        }
    }

    // -------------------------------------------------------------------------
    // Opponent Pod Strip (Compact summary of non-viewing seats)
    // -------------------------------------------------------------------------
    function renderOpponentStrip(state) {
        var container = document.getElementById('opponentStripContainer');
        if (!container) return;
        if (state.seats.length <= 1) {
            container.classList.add('hidden');
            return;
        }
        container.classList.remove('hidden');
        container.innerHTML = '';

        for (var i = 0; i < state.seats.length; i++) {
            if (i === state.viewing_seat) continue;
            var opp = state.seats[i];

            var oppCard = document.createElement('div');
            oppCard.className = 'flex-1 min-w-[140px] bg-[#141B26] border border-[#232F42] p-1.5 flex items-center justify-between text-xs font-mono';

            var cmdrImg = opp.commander_art
                ? '<img src="' + opp.commander_art + '" class="w-6 h-6 rounded object-cover flex-shrink-0 border border-slate-700">'
                : '<div class="w-6 h-6 rounded bg-slate-800 flex items-center justify-center text-[10px] font-bold text-slate-400 flex-shrink-0">P' + (i + 1) + '</div>';

            var infoHtml = '<div class="min-w-0 flex-1 px-1.5">' +
                '<div class="truncate text-[11px] text-slate-300 font-bold">' + opp.name + '</div>' +
                '<div class="flex items-center gap-2 text-[10px] text-slate-400">' +
                '<span class="text-emerald-400 font-bold">❤ ' + opp.life + '</span>' +
                '<span>🂠 ' + opp.zones.hand.length + '</span>' +
                '<span>⚔ ' + opp.zones.battlefield.length + '</span>' +
                '</div></div>';

            var switchBtn = document.createElement('button');
            switchBtn.type = 'button';
            switchBtn.className = 'px-2 py-1 bg-cyan-500/20 hover:bg-cyan-500 hover:text-slate-950 text-cyan-300 text-[10px] font-bold uppercase transition flex-shrink-0';
            switchBtn.textContent = 'Control';

            (function (oppIndex) {
                switchBtn.onclick = function () {
                    haptic(15);
                    coord.dispatch({ type: 'SWITCH_SEAT', payload: { seat: oppIndex } });
                    renderAll();
                };
            })(i);

            oppCard.innerHTML = cmdrImg + infoHtml;
            oppCard.appendChild(switchBtn);
            container.appendChild(oppCard);
        }
    }

    // -------------------------------------------------------------------------
    // Battlefield Rendering (Smart Land Stacking + Permanents)
    // -------------------------------------------------------------------------
    function renderBattlefield(state) {
        var currentSeat = state.seats[state.viewing_seat];
        var nonlandsRow = document.getElementById('battlefieldNonlandsRow');
        var landsRow = document.getElementById('battlefieldLandsRow');
        if (!currentSeat || !nonlandsRow || !landsRow) return;

        nonlandsRow.innerHTML = '';
        landsRow.innerHTML = '';

        var landsGrouped = {}; // refKey -> [instIds]
        var nonlandsList = [];

        for (var i = 0; i < currentSeat.zones.battlefield.length; i++) {
            var instId = currentSeat.zones.battlefield[i];
            var card = state.cards[instId];
            if (!card) continue;
            var ref = state.card_refs[card.card_ref] || {};
            var typeLine = (ref.type_line || '').toLowerCase();

            if (typeLine.includes('land')) {
                if (!landsGrouped[card.card_ref]) landsGrouped[card.card_ref] = [];
                landsGrouped[card.card_ref].push(instId);
            } else {
                nonlandsList.push(instId);
            }
        }

        // 1. Render Non-land Permanents (Creatures, Artifacts, Enchantments)
        if (nonlandsList.length === 0) {
            nonlandsRow.innerHTML = '<div class="text-[11px] text-slate-500 font-mono py-4 text-center w-full">No active non-land permanents on board.</div>';
        } else {
            for (var j = 0; j < nonlandsList.length; j++) {
                var cInstId = nonlandsList[j];
                var cardElem = createPermanentCardElement(state, cInstId);
                nonlandsRow.appendChild(cardElem);
            }
        }

        // 2. Render Lands with Smart Stacking
        var landKeys = Object.keys(landsGrouped);
        if (landKeys.length === 0) {
            landsRow.innerHTML = '<div class="text-[11px] text-slate-500 font-mono py-2 text-center w-full">No lands played yet.</div>';
        } else {
            for (var k = 0; k < landKeys.length; k++) {
                var refKey = landKeys[k];
                var stackInstIds = landsGrouped[refKey];
                var stackElem = createLandStackElement(state, refKey, stackInstIds);
                landsRow.appendChild(stackElem);
            }
        }
    }

    function createPermanentCardElement(state, instId) {
        var card = state.cards[instId];
        var ref = state.card_refs[card.card_ref] || {};
        var pt = PlaytestEngine.getEffectivePT(state, instId);

        var cardDiv = document.createElement('div');
        cardDiv.className = 'relative flex-shrink-0 w-24 sm:w-28 rounded border cursor-pointer select-none transition-all duration-150 ' +
            (card.tapped ? 'rotate-90 opacity-70 border-amber-500/60 my-2' : 'border-[#2A374A] hover:border-cyan-400') +
            (state.combat && state.combat.attackers && state.combat.attackers[instId] ? ' ring-2 ring-red-500 shadow-[0_0_15px_rgba(239,68,68,0.5)]' : '');

        var imgSrc = ref.small_image_uri || ref.image_uri || '';
        var imgHtml = imgSrc
            ? '<img src="' + imgSrc + '" class="w-full h-auto aspect-[5/7] object-cover rounded pointer-events-none" loading="lazy">'
            : '<div class="w-full aspect-[5/7] bg-slate-800 p-2 text-[10px] font-mono text-white flex flex-col justify-between rounded">' +
              '<div class="font-bold truncate">' + ref.name + '</div>' +
              '<div class="text-[9px] text-slate-400">' + ref.type_line + '</div></div>';

        var ptBadge = pt.hasPT
            ? '<div class="absolute bottom-1 right-1 bg-slate-950/90 border border-slate-700 px-1 py-0.2 rounded text-[10px] font-mono font-bold text-white shadow">' +
              pt.power + '/' + pt.toughness + '</div>'
            : '';

        var countersBadge = '';
        if (card.counters && Object.keys(card.counters).length > 0) {
            var counterText = [];
            for (var cType in card.counters) {
                counterText.push('+' + card.counters[cType]);
            }
            countersBadge = '<div class="absolute top-1 left-1 bg-cyan-950/90 border border-cyan-400 px-1 py-0.2 rounded text-[9px] font-mono font-bold text-cyan-300">' +
                counterText.join(' ') + '</div>';
        }

        var sickBadge = card.summoning_sick
            ? '<div class="absolute top-1 right-1 bg-amber-950/90 border border-amber-500 px-1 py-0.2 rounded text-[8px] font-mono font-bold text-amber-400">ZZZ</div>'
            : '';

        var tapBadge = card.tapped
            ? '<div class="absolute inset-0 flex items-center justify-center font-mono font-black text-amber-400 text-xs bg-slate-950/40 rounded">TAPPED</div>'
            : '';

        cardDiv.innerHTML = imgHtml + ptBadge + countersBadge + sickBadge + tapBadge;

        // Click to tap/untap or toggle combat attacker/blocker
        cardDiv.onclick = function (e) {
            e.stopPropagation();
            haptic(12);

            if (state.combat) {
                if (state.combat.step === 'attackers' && state.viewing_seat === state.combat.attacking_seat) {
                    coord.dispatch({ type: 'COMBAT_TOGGLE_ATTACKER', payload: { instance_id: instId } });
                    renderAll();
                    return;
                }
            }

            // Normal tap/untap
            coord.dispatch({ type: 'TAP_PERMANENT', payload: { instance_id: instId } });
            renderAll();
        };

        // Long-press or context menu opens action sheet
        cardDiv.oncontextmenu = function (e) {
            e.preventDefault();
            openPermanentActionSheet(state, instId);
        };

        return cardDiv;
    }

    function createLandStackElement(state, refKey, instIds) {
        var ref = state.card_refs[refKey] || { name: 'Land' };
        var untappedCount = 0;
        for (var i = 0; i < instIds.length; i++) {
            if (!state.cards[instIds[i]].tapped) untappedCount++;
        }

        var stackDiv = document.createElement('div');
        stackDiv.className = 'flex items-center justify-between gap-2 bg-[#141B26] border border-[#232F42] px-2.5 py-1.5 rounded font-mono text-xs flex-1 min-w-[150px] max-w-[240px]';

        var labelHtml = '<div class="min-w-0 flex-1">' +
            '<div class="truncate font-bold text-slate-200 text-[11px]">' + ref.name + ' (' + instIds.length + ')</div>' +
            '<div class="text-[9px] text-cyan-400">' + untappedCount + ' available</div>' +
            '</div>';

        var buttonsDiv = document.createElement('div');
        buttonsDiv.className = 'flex items-center gap-1 flex-shrink-0';

        var tap1Btn = document.createElement('button');
        tap1Btn.type = 'button';
        tap1Btn.className = 'px-1.5 py-1 bg-slate-800 hover:bg-cyan-500 hover:text-slate-950 text-cyan-300 text-[10px] font-bold border border-slate-700 rounded transition';
        tap1Btn.textContent = 'Tap 1';
        tap1Btn.onclick = function (e) {
            e.stopPropagation();
            haptic(12);
            for (var i = 0; i < instIds.length; i++) {
                if (!state.cards[instIds[i]].tapped) {
                    coord.dispatch({ type: 'TAP_PERMANENT', payload: { instance_id: instIds[i], state: true } });
                    renderAll();
                    return;
                }
            }
        };

        var tapAllBtn = document.createElement('button');
        tapAllBtn.type = 'button';
        tapAllBtn.className = 'px-1.5 py-1 bg-slate-800 hover:bg-amber-500 hover:text-slate-950 text-amber-300 text-[10px] font-bold border border-slate-700 rounded transition';
        tapAllBtn.textContent = untappedCount > 0 ? 'All' : 'Untap';
        tapAllBtn.onclick = function (e) {
            e.stopPropagation();
            haptic(15);
            var shouldTap = (untappedCount > 0);
            for (var i = 0; i < instIds.length; i++) {
                coord.dispatch({ type: 'TAP_PERMANENT', payload: { instance_id: instIds[i], state: shouldTap } });
            }
            renderAll();
        };

        buttonsDiv.appendChild(tap1Btn);
        buttonsDiv.appendChild(tapAllBtn);

        stackDiv.innerHTML = labelHtml;
        stackDiv.appendChild(buttonsDiv);
        return stackDiv;
    }

    // -------------------------------------------------------------------------
    // Zone Bar & Primary Actions
    // -------------------------------------------------------------------------
    function renderZoneBar(state) {
        var currentSeat = state.seats[state.viewing_seat];
        if (!currentSeat) return;

        // 1. Life Counter
        var lifeVal = document.getElementById('hudLifeValue');
        if (lifeVal) lifeVal.textContent = currentSeat.life;

        // 2. Command Zone Tax
        var cmdrZoneBtn = document.getElementById('zoneCommandBtn');
        if (cmdrZoneBtn) {
            var casts = 0;
            for (var cId in (currentSeat.commander_casts || {})) {
                casts += currentSeat.commander_casts[cId];
            }
            var tax = casts * 2;
            cmdrZoneBtn.innerHTML = '⚔ Cmd (' + currentSeat.zones.command.length + ')' +
                (tax > 0 ? ' <span class="text-amber-400">+' + tax + '</span>' : '');
            cmdrZoneBtn.onclick = function () {
                openZoneSheet(state, 'command', 'Command Zone');
            };
        }

        // 3. Library Button
        var libBtn = document.getElementById('zoneLibraryBtn');
        if (libBtn) {
            libBtn.textContent = '🂠 Lib (' + currentSeat.zones.library.length + ')';
            libBtn.onclick = function () {
                openLibrarySheet(state);
            };
        }

        // 4. Graveyard Button
        var gyBtn = document.getElementById('zoneGraveyardBtn');
        if (gyBtn) {
            gyBtn.textContent = '💀 GY (' + currentSeat.zones.graveyard.length + ')';
            gyBtn.onclick = function () {
                openZoneSheet(state, 'graveyard', 'Graveyard');
            };
        }

        // 5. Exile Button
        var exileBtn = document.getElementById('zoneExileBtn');
        if (exileBtn) {
            exileBtn.textContent = '✨ Exile (' + currentSeat.zones.exile.length + ')';
            exileBtn.onclick = function () {
                openZoneSheet(state, 'exile', 'Exile Zone');
            };
        }

        // 6. Next Turn / Combat Button
        var stepBtn = document.getElementById('primaryStepButton');
        if (stepBtn) {
            if (!state.combat) {
                if (state.phase === 'main1') {
                    stepBtn.textContent = '⚔ Combat';
                    stepBtn.className = 'flex-1 py-2 bg-gradient-to-r from-red-600 to-amber-600 text-white font-mono font-bold text-xs uppercase shadow transition';
                    stepBtn.onclick = function () {
                        haptic(15);
                        coord.dispatch({ type: 'COMBAT_START' });
                        renderAll();
                    };
                } else {
                    stepBtn.textContent = '▶ Pass Turn';
                    stepBtn.className = 'flex-1 py-2 bg-cyan-600 hover:bg-cyan-500 text-slate-950 font-mono font-black text-xs uppercase shadow transition';
                    stepBtn.onclick = function () {
                        haptic(15);
                        coord.dispatch({ type: 'PASS_TURN' });
                        renderAll();
                        showToast('Turn passed.', true);
                    };
                }
            } else {
                stepBtn.textContent = 'In Combat';
                stepBtn.className = 'flex-1 py-2 bg-red-700 text-white font-mono font-bold text-xs uppercase';
                stepBtn.onclick = null;
            }
        }
    }

    // -------------------------------------------------------------------------
    // Hand Dock & Pass-Device Privacy Shield
    // -------------------------------------------------------------------------
    function renderHandDock(state) {
        var currentSeat = state.seats[state.viewing_seat];
        var dock = document.getElementById('handDockContainer');
        var privacyCover = document.getElementById('handPrivacyCover');
        var handCardsContainer = document.getElementById('handCardsCarousel');
        var handCountBadge = document.getElementById('handCountBadge');
        if (!currentSeat || !dock) return;

        if (handCountBadge) handCountBadge.textContent = currentSeat.zones.hand.length;

        // Privacy Shield Check
        var hideHands = !!state.settings.hide_hands;
        var isRevealed = !!state.hand_revealed;

        if (hideHands && !isRevealed) {
            if (privacyCover) privacyCover.classList.remove('hidden');
            if (handCardsContainer) handCardsContainer.classList.add('hidden');

            var passTarget = document.getElementById('passDeviceSeatName');
            if (passTarget) passTarget.textContent = currentSeat.name;

            var revealBtn = document.getElementById('btnRevealHand');
            if (revealBtn) {
                revealBtn.onclick = function () {
                    haptic(15);
                    coord.dispatch({ type: 'REVEAL_HAND' });
                    renderAll();
                };
            }
            return;
        }

        // Revealed Hand Cards
        if (privacyCover) privacyCover.classList.add('hidden');
        if (handCardsContainer) {
            handCardsContainer.classList.remove('hidden');
            handCardsContainer.innerHTML = '';

            if (currentSeat.zones.hand.length === 0) {
                handCardsContainer.innerHTML = '<div class="text-xs text-slate-500 font-mono py-4 text-center w-full">Hand is empty.</div>';
                return;
            }

            for (var i = 0; i < currentSeat.zones.hand.length; i++) {
                var instId = currentSeat.zones.hand[i];
                var cardElem = createHandCardElement(state, instId);
                handCardsContainer.appendChild(cardElem);
            }
        }
    }

    function createHandCardElement(state, instId) {
        var card = state.cards[instId];
        var ref = state.card_refs[card.card_ref] || {};

        var cardDiv = document.createElement('div');
        cardDiv.className = 'relative flex-shrink-0 w-24 sm:w-28 rounded border border-[#2A374A] hover:border-cyan-400 cursor-pointer select-none transition-transform hover:-translate-y-1';

        var imgSrc = ref.small_image_uri || ref.image_uri || '';
        var imgHtml = imgSrc
            ? '<img src="' + imgSrc + '" class="w-full h-auto aspect-[5/7] object-cover rounded pointer-events-none" loading="lazy">'
            : '<div class="w-full aspect-[5/7] bg-slate-800 p-2 text-[10px] font-mono text-white flex flex-col justify-between rounded">' +
              '<div class="font-bold truncate">' + ref.name + '</div>' +
              '<div class="text-[9px] text-cyan-300">' + (ref.mana_cost || '') + '</div></div>';

        cardDiv.innerHTML = imgHtml;

        // Tap card opens Action Sheet
        cardDiv.onclick = function () {
            haptic(12);
            openHandCardActionSheet(state, instId);
        };

        return cardDiv;
    }

    // -------------------------------------------------------------------------
    // Hand Card Action Sheet (Native-feeling mobile drawer)
    // -------------------------------------------------------------------------
    function openHandCardActionSheet(state, instId) {
        var card = state.cards[instId];
        var ref = state.card_refs[card.card_ref] || {};
        var typeLine = (ref.type_line || '').toLowerCase();
        var isLand = typeLine.includes('land');

        var sheet = document.getElementById('genericActionSheet');
        var title = document.getElementById('actionSheetTitle');
        var body = document.getElementById('actionSheetBody');
        if (!sheet || !body) return;

        title.textContent = ref.name + (ref.mana_cost ? ' (' + ref.mana_cost + ')' : '');
        body.innerHTML = '';

        var actions = [];

        // 1. Play Untapped
        actions.push({
            label: isLand ? 'Play Land (Untapped)' : 'Cast Spell (Play to Board)',
            color: 'bg-cyan-600 hover:bg-cyan-500 text-slate-950 font-bold',
            fn: function () {
                coord.dispatch({ type: 'PLAY_CARD', payload: { instance_id: instId, tapped: false } });
                closeActionSheet();
                renderAll();
                showToast('Played ' + ref.name, true);
            }
        });

        // 2. Play Tapped
        actions.push({
            label: 'Play Tapped',
            color: 'bg-slate-800 hover:bg-slate-700 text-amber-300 font-semibold',
            fn: function () {
                coord.dispatch({ type: 'PLAY_CARD', payload: { instance_id: instId, tapped: true } });
                closeActionSheet();
                renderAll();
                showToast('Played ' + ref.name + ' (tapped)', true);
            }
        });

        // 3. Discard to Graveyard
        actions.push({
            label: 'Discard to Graveyard',
            color: 'bg-slate-800 hover:bg-slate-700 text-slate-300',
            fn: function () {
                coord.dispatch({
                    type: 'MOVE_CARD',
                    payload: { instance_id: instId, from_zone: 'hand', to_zone: 'graveyard' }
                });
                closeActionSheet();
                renderAll();
                showToast('Discarded ' + ref.name, true);
            }
        });

        // 4. Exile
        actions.push({
            label: 'Exile',
            color: 'bg-slate-800 hover:bg-slate-700 text-purple-300',
            fn: function () {
                coord.dispatch({
                    type: 'MOVE_CARD',
                    payload: { instance_id: instId, from_zone: 'hand', to_zone: 'exile' }
                });
                closeActionSheet();
                renderAll();
                showToast('Exiled ' + ref.name, true);
            }
        });

        for (var i = 0; i < actions.length; i++) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'w-full py-2.5 px-4 rounded text-xs font-mono uppercase transition text-left ' + actions[i].color;
            btn.textContent = actions[i].label;
            (function (act) {
                btn.onclick = function () {
                    haptic(15);
                    act.fn();
                };
            })(actions[i]);
            body.appendChild(btn);
        }

        // Card Oracle Text details
        if (ref.oracle_text) {
            var details = document.createElement('div');
            details.className = 'p-3 bg-slate-900 border border-slate-800 rounded text-xs font-mono text-slate-300 mt-2 leading-relaxed';
            details.textContent = ref.oracle_text;
            body.appendChild(details);
        }

        sheet.classList.remove('hidden');
    }

    function openPermanentActionSheet(state, instId) {
        var card = state.cards[instId];
        var ref = state.card_refs[card.card_ref] || {};

        var sheet = document.getElementById('genericActionSheet');
        var title = document.getElementById('actionSheetTitle');
        var body = document.getElementById('actionSheetBody');
        if (!sheet || !body) return;

        title.textContent = ref.name + ' (Battlefield Actions)';
        body.innerHTML = '';

        var actions = [
            {
                label: '+1/+1 Counter (+1)',
                color: 'bg-slate-800 text-cyan-300',
                fn: function () {
                    coord.dispatch({ type: 'ADJUST_COUNTERS', payload: { instance_id: instId, counter_type: 'p1p1', delta: 1 } });
                    closeActionSheet();
                    renderAll();
                }
            },
            {
                label: '-1/-1 Counter (-1)',
                color: 'bg-slate-800 text-slate-400',
                fn: function () {
                    coord.dispatch({ type: 'ADJUST_COUNTERS', payload: { instance_id: instId, counter_type: 'm1m1', delta: 1 } });
                    closeActionSheet();
                    renderAll();
                }
            },
            {
                label: 'Destroy to Graveyard',
                color: 'bg-red-950/40 text-red-400 border border-red-800/50',
                fn: function () {
                    if (card.is_commander) {
                        coord.dispatch({ type: 'MOVE_CARD', payload: { instance_id: instId, from_zone: 'battlefield', to_zone: 'command' } });
                        showToast('Commander returned to Command Zone.', true);
                    } else {
                        coord.dispatch({ type: 'MOVE_CARD', payload: { instance_id: instId, from_zone: 'battlefield', to_zone: 'graveyard' } });
                        showToast('Moved to Graveyard.', true);
                    }
                    closeActionSheet();
                    renderAll();
                }
            },
            {
                label: 'Exile',
                color: 'bg-purple-950/40 text-purple-400 border border-purple-800/50',
                fn: function () {
                    if (card.is_commander) {
                        coord.dispatch({ type: 'MOVE_CARD', payload: { instance_id: instId, from_zone: 'battlefield', to_zone: 'command' } });
                        showToast('Commander returned to Command Zone.', true);
                    } else {
                        coord.dispatch({ type: 'MOVE_CARD', payload: { instance_id: instId, from_zone: 'battlefield', to_zone: 'exile' } });
                        showToast('Exiled.', true);
                    }
                    closeActionSheet();
                    renderAll();
                }
            },
            {
                label: 'Bounce to Hand',
                color: 'bg-slate-800 text-slate-300',
                fn: function () {
                    coord.dispatch({ type: 'MOVE_CARD', payload: { instance_id: instId, from_zone: 'battlefield', to_zone: 'hand' } });
                    closeActionSheet();
                    renderAll();
                    showToast('Returned to Hand.', true);
                }
            }
        ];

        for (var i = 0; i < actions.length; i++) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'w-full py-2.5 px-4 rounded text-xs font-mono uppercase transition text-left ' + actions[i].color;
            btn.textContent = actions[i].label;
            (function (act) {
                btn.onclick = function () {
                    haptic(15);
                    act.fn();
                };
            })(actions[i]);
            body.appendChild(btn);
        }

        sheet.classList.remove('hidden');
    }

    function closeActionSheet() {
        var sheet = document.getElementById('genericActionSheet');
        if (sheet) sheet.classList.add('hidden');
    }

    // -------------------------------------------------------------------------
    // Zone Inspection Sheets (Library, Graveyard, Exile, Command)
    // -------------------------------------------------------------------------
    function openZoneSheet(state, zoneName, titleText) {
        var currentSeat = state.seats[state.viewing_seat];
        var sheet = document.getElementById('zoneInspectionSheet');
        var title = document.getElementById('zoneSheetTitle');
        var list = document.getElementById('zoneCardsList');
        if (!sheet || !list || !currentSeat) return;

        title.textContent = titleText + ' (' + currentSeat.zones[zoneName].length + ')';
        list.innerHTML = '';

        var instIds = currentSeat.zones[zoneName];
        if (instIds.length === 0) {
            list.innerHTML = '<div class="text-xs text-slate-500 font-mono py-8 text-center w-full">Zone is currently empty.</div>';
        } else {
            for (var i = 0; i < instIds.length; i++) {
                var instId = instIds[i];
                var card = state.cards[instId];
                var ref = state.card_refs[card.card_ref] || {};

                var itemDiv = document.createElement('div');
                itemDiv.className = 'flex items-center justify-between p-2 bg-[#141B26] border border-[#232F42] rounded text-xs font-mono';

                var label = '<div class="min-w-0 flex-1 truncate font-bold text-slate-200">' + ref.name +
                    (ref.type_line ? ' <span class="text-[10px] text-slate-500 font-normal">(' + ref.type_line + ')</span>' : '') +
                    '</div>';

                var btnGroup = document.createElement('div');
                btnGroup.className = 'flex items-center gap-1.5 flex-shrink-0';

                // Play / Cast button
                var toBoardBtn = document.createElement('button');
                toBoardBtn.type = 'button';
                toBoardBtn.className = 'px-2 py-1 bg-cyan-600 hover:bg-cyan-500 text-slate-950 font-bold rounded text-[10px] uppercase';
                toBoardBtn.textContent = zoneName === 'command' ? 'Cast' : 'To Board';
                (function (id) {
                    toBoardBtn.onclick = function () {
                        haptic(15);
                        if (zoneName === 'command') {
                            coord.dispatch({ type: 'CAST_COMMANDER', payload: { instance_id: id } });
                        } else {
                            coord.dispatch({ type: 'MOVE_CARD', payload: { instance_id: id, from_zone: zoneName, to_zone: 'battlefield' } });
                        }
                        sheet.classList.add('hidden');
                        renderAll();
                    };
                })(instId);

                // To Hand button
                var toHandBtn = document.createElement('button');
                toHandBtn.type = 'button';
                toHandBtn.className = 'px-2 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 font-bold rounded text-[10px] uppercase';
                toHandBtn.textContent = 'To Hand';
                (function (id) {
                    toHandBtn.onclick = function () {
                        haptic(15);
                        coord.dispatch({ type: 'MOVE_CARD', payload: { instance_id: id, from_zone: zoneName, to_zone: 'hand' } });
                        sheet.classList.add('hidden');
                        renderAll();
                    };
                })(instId);

                btnGroup.appendChild(toBoardBtn);
                btnGroup.appendChild(toHandBtn);

                itemDiv.innerHTML = label;
                itemDiv.appendChild(btnGroup);
                list.appendChild(itemDiv);
            }
        }

        sheet.classList.remove('hidden');
    }

    function openLibrarySheet(state) {
        var currentSeat = state.seats[state.viewing_seat];
        var sheet = document.getElementById('zoneInspectionSheet');
        var title = document.getElementById('zoneSheetTitle');
        var list = document.getElementById('zoneCardsList');
        if (!sheet || !list || !currentSeat) return;

        title.textContent = 'Library Controls (' + currentSeat.zones.library.length + ' cards)';
        list.innerHTML = '';

        // Quick Action Controls at top
        var actionsDiv = document.createElement('div');
        actionsDiv.className = 'grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4';

        var actSpecs = [
            { label: 'Draw 1', fn: function () { coord.dispatch({ type: 'DRAW_CARD', payload: { count: 1 } }); } },
            { label: 'Draw 3', fn: function () { coord.dispatch({ type: 'DRAW_CARD', payload: { count: 3 } }); } },
            { label: 'Mill 3', fn: function () {
                for (var i = 0; i < 3; i++) {
                    if (currentSeat.zones.library.length > 0) {
                        var id = currentSeat.zones.library[0];
                        coord.dispatch({ type: 'MOVE_CARD', payload: { instance_id: id, from_zone: 'library', to_zone: 'graveyard' } });
                    }
                }
            }},
            { label: 'Shuffle', fn: function () {
                // In place shuffle
                for (var i = currentSeat.zones.library.length - 1; i > 0; i--) {
                    var j = Math.floor(Math.random() * (i + 1));
                    var tmp = currentSeat.zones.library[i];
                    currentSeat.zones.library[i] = currentSeat.zones.library[j];
                    currentSeat.zones.library[j] = tmp;
                }
                showToast('Library shuffled.');
            }}
        ];

        for (var a = 0; a < actSpecs.length; a++) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'py-2 px-3 bg-slate-800 hover:bg-cyan-500 hover:text-slate-950 text-cyan-300 font-mono font-bold text-xs uppercase rounded transition border border-slate-700';
            btn.textContent = actSpecs[a].label;
            (function (spec) {
                btn.onclick = function () {
                    haptic(15);
                    spec.fn();
                    sheet.classList.add('hidden');
                    renderAll();
                };
            })(actSpecs[a]);
            actionsDiv.appendChild(btn);
        }

        list.appendChild(actionsDiv);

        // Search deck list A-Z
        var searchInput = document.createElement('input');
        searchInput.type = 'text';
        searchInput.placeholder = 'Search library cards...';
        searchInput.className = 'w-full px-3 py-2 bg-slate-900 border border-slate-700 text-white font-mono text-xs rounded mb-3';
        list.appendChild(searchInput);

        var cardsWrapper = document.createElement('div');
        cardsWrapper.className = 'space-y-1.5 max-h-[50vh] overflow-y-auto modal-scrollable';
        list.appendChild(cardsWrapper);

        function renderLibraryCards(filter) {
            cardsWrapper.innerHTML = '';
            var q = (filter || '').toLowerCase();
            for (var i = 0; i < currentSeat.zones.library.length; i++) {
                var instId = currentSeat.zones.library[i];
                var card = state.cards[instId];
                var ref = state.card_refs[card.card_ref] || {};
                if (q && !ref.name.toLowerCase().includes(q)) continue;

                var row = document.createElement('div');
                row.className = 'flex items-center justify-between p-2 bg-[#141B26] border border-[#232F42] rounded text-xs font-mono';
                row.innerHTML = '<span class="font-bold text-slate-200 truncate flex-1">' + ref.name + '</span>';

                var toHand = document.createElement('button');
                toHand.type = 'button';
                toHand.className = 'px-2 py-1 bg-cyan-600 hover:bg-cyan-500 text-slate-950 font-bold rounded text-[10px] uppercase ml-2';
                toHand.textContent = 'To Hand';
                (function (id) {
                    toHand.onclick = function () {
                        haptic(15);
                        coord.dispatch({ type: 'MOVE_CARD', payload: { instance_id: id, from_zone: 'library', to_zone: 'hand' } });
                        sheet.classList.add('hidden');
                        renderAll();
                    };
                })(instId);

                row.appendChild(toHand);
                cardsWrapper.appendChild(row);
            }
        }

        searchInput.oninput = function () {
            renderLibraryCards(this.value);
        };

        renderLibraryCards('');
        sheet.classList.remove('hidden');
    }

    // -------------------------------------------------------------------------
    // Guided Step-by-Step Combat Stepper
    // -------------------------------------------------------------------------
    function renderCombatStepper(state) {
        var stepper = document.getElementById('combatStepperSheet');
        if (!stepper) return;

        if (!state.combat) {
            stepper.classList.add('hidden');
            return;
        }

        stepper.classList.remove('hidden');
        var step = state.combat.step; // 'attackers' | 'blockers' | 'tricks' | 'preview'
        var content = document.getElementById('combatStepperContent');
        if (!content) return;

        if (step === 'attackers') {
            var atkIds = Object.keys(state.combat.attackers);
            content.innerHTML = '<div class="space-y-2">' +
                '<div class="flex items-center justify-between">' +
                '<span class="text-xs font-mono font-bold text-amber-400 uppercase">Step 1: Declare Attackers</span>' +
                '<span class="text-xs font-mono text-slate-300">' + atkIds.length + ' Selected</span>' +
                '</div>' +
                '<p class="text-[11px] text-slate-400 font-mono">Tap creatures on your battlefield to attack. Creatures with Vigilance won\'t tap.</p>' +
                '<div class="flex items-center gap-2 pt-1">' +
                '<button type="button" id="btnCombatAttackAll" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-cyan-300 font-mono text-xs uppercase rounded">Attack All</button>' +
                '<button type="button" id="btnCombatConfirmAtk" class="flex-1 py-1.5 bg-red-600 hover:bg-red-500 text-white font-mono font-bold text-xs uppercase rounded">Confirm Attackers &rarr;</button>' +
                '<button type="button" id="btnCombatCancel" class="px-3 py-1.5 bg-slate-800 text-slate-400 font-mono text-xs uppercase rounded">Cancel</button>' +
                '</div></div>';

            document.getElementById('btnCombatAttackAll').onclick = function () {
                haptic(15);
                coord.dispatch({ type: 'COMBAT_ATTACK_ALL' });
                renderAll();
            };
            document.getElementById('btnCombatConfirmAtk').onclick = function () {
                haptic(15);
                coord.dispatch({ type: 'COMBAT_CONFIRM_ATTACKERS' });
                renderAll();
            };
            document.getElementById('btnCombatCancel').onclick = function () {
                haptic(15);
                coord.dispatch({ type: 'COMBAT_CANCEL' });
                renderAll();
            };
        } else if (step === 'blockers') {
            content.innerHTML = '<div class="space-y-2">' +
                '<div class="flex items-center justify-between">' +
                '<span class="text-xs font-mono font-bold text-blue-400 uppercase">Step 2: Declare Blockers</span>' +
                '<button type="button" id="btnNoBlocks" class="text-xs font-mono text-slate-400 hover:text-white uppercase underline">No Blocks</button>' +
                '</div>' +
                '<p class="text-[11px] text-slate-400 font-mono">Defending players may assign blockers or skip to damage.</p>' +
                '<div class="flex items-center gap-2 pt-1">' +
                '<button type="button" id="btnConfirmBlockers" class="flex-1 py-1.5 bg-blue-600 hover:bg-blue-500 text-white font-mono font-bold text-xs uppercase rounded">Confirm Blockers &rarr;</button>' +
                '</div></div>';

            document.getElementById('btnNoBlocks').onclick = function () {
                haptic(15);
                coord.dispatch({ type: 'COMBAT_CONFIRM_BLOCKERS' });
                renderAll();
            };
            document.getElementById('btnConfirmBlockers').onclick = function () {
                haptic(15);
                coord.dispatch({ type: 'COMBAT_CONFIRM_BLOCKERS' });
                renderAll();
            };
        } else if (step === 'tricks') {
            content.innerHTML = '<div class="space-y-2">' +
                '<div class="flex items-center justify-between">' +
                '<span class="text-xs font-mono font-bold text-purple-400 uppercase">Step 3: Combat Tricks</span>' +
                '</div>' +
                '<p class="text-[11px] text-slate-400 font-mono">Cast combat instants or activate abilities before damage resolves.</p>' +
                '<div class="flex items-center gap-2 pt-1">' +
                '<button type="button" id="btnConfirmTricks" class="flex-1 py-1.5 bg-purple-600 hover:bg-purple-500 text-white font-mono font-bold text-xs uppercase rounded">Proceed to Damage &rarr;</button>' +
                '</div></div>';

            document.getElementById('btnConfirmTricks').onclick = function () {
                haptic(15);
                coord.dispatch({ type: 'COMBAT_CONFIRM_TRICKS' });
                renderAll();
            };
        } else if (step === 'preview') {
            var prev = state.combat.preview || {};
            content.innerHTML = '<div class="space-y-2">' +
                '<div class="flex items-center justify-between">' +
                '<span class="text-xs font-mono font-bold text-emerald-400 uppercase">Step 4: Damage Preview</span>' +
                '</div>' +
                '<div class="p-2 bg-slate-900 border border-slate-800 rounded font-mono text-xs text-slate-300 leading-relaxed">' +
                (prev.summary || 'Damage calculated.') +
                '</div>' +
                '<div class="flex items-center gap-2 pt-1">' +
                '<button type="button" id="btnApplyCombat" class="flex-1 py-2 bg-emerald-600 hover:bg-emerald-500 text-slate-950 font-mono font-black text-xs uppercase rounded shadow">Apply Damage & Conclude</button>' +
                '<button type="button" id="btnCancelCombatPrev" class="px-3 py-2 bg-slate-800 text-slate-400 font-mono text-xs uppercase rounded">Cancel</button>' +
                '</div></div>';

            document.getElementById('btnApplyCombat').onclick = function () {
                haptic(20);
                coord.dispatch({ type: 'COMBAT_APPLY' });
                renderAll();
                showToast('Combat damage resolved.', true);
            };
            document.getElementById('btnCancelCombatPrev').onclick = function () {
                haptic(15);
                coord.dispatch({ type: 'COMBAT_CANCEL' });
                renderAll();
            };
        }
    }

    // -------------------------------------------------------------------------
    // Life Adjuster Bottom Sheet
    // -------------------------------------------------------------------------
    function openLifeCounterSheet() {
        var state = coord.state;
        var currentSeat = state.seats[state.viewing_seat];
        var sheet = document.getElementById('lifeCounterSheet');
        if (!sheet || !currentSeat) return;

        var display = document.getElementById('lifePadTotal');
        var poisonDisplay = document.getElementById('poisonPadTotal');
        var matrix = document.getElementById('commanderDamageMatrix');

        function updatePad() {
            if (display) display.textContent = currentSeat.life;
            if (poisonDisplay) poisonDisplay.textContent = currentSeat.poison;
            if (matrix) {
                matrix.innerHTML = '';
                for (var s = 0; s < state.seats.length; s++) {
                    if (s === state.viewing_seat) continue;
                    var opp = state.seats[s];
                    var curDmg = (currentSeat.commander_damage && currentSeat.commander_damage[String(s)]) || 0;
                    var isLethal = curDmg >= 21;

                    var row = document.createElement('div');
                    row.className = 'flex items-center justify-between p-2 bg-[#141B26] border ' +
                        (isLethal ? 'border-red-500 bg-red-950/30' : 'border-[#232F42]') + ' rounded text-xs font-mono';

                    row.innerHTML = '<span class="font-bold text-slate-200 truncate flex-1">' + opp.name + '</span>' +
                        '<span class="font-bold ' + (isLethal ? 'text-red-400' : 'text-slate-300') + '">' + curDmg + '/21</span>';

                    var adjustBtns = document.createElement('div');
                    adjustBtns.className = 'flex items-center gap-1 ml-2';

                    var plusBtn = document.createElement('button');
                    plusBtn.type = 'button';
                    plusBtn.className = 'px-2 py-0.5 bg-slate-800 text-cyan-300 rounded font-bold';
                    plusBtn.textContent = '+';
                    (function (srcSeat) {
                        plusBtn.onclick = function () {
                            haptic(12);
                            coord.dispatch({ type: 'ADJUST_COMMANDER_DAMAGE', payload: { target_seat: state.viewing_seat, source_seat: srcSeat, delta: 1 } });
                            updatePad();
                            renderAll();
                        };
                    })(s);

                    var minusBtn = document.createElement('button');
                    minusBtn.type = 'button';
                    minusBtn.className = 'px-2 py-0.5 bg-slate-800 text-slate-400 rounded font-bold';
                    minusBtn.textContent = '-';
                    (function (srcSeat) {
                        minusBtn.onclick = function () {
                            haptic(12);
                            coord.dispatch({ type: 'ADJUST_COMMANDER_DAMAGE', payload: { target_seat: state.viewing_seat, source_seat: srcSeat, delta: -1 } });
                            updatePad();
                            renderAll();
                        };
                    })(s);

                    adjustBtns.appendChild(minusBtn);
                    adjustBtns.appendChild(plusBtn);
                    row.appendChild(adjustBtns);
                    matrix.appendChild(row);
                }
            }
        }

        updatePad();
        sheet.classList.remove('hidden');
    }

    // -------------------------------------------------------------------------
    // Initialization & Event Wireups
    // -------------------------------------------------------------------------
    window.initPlaytestArena = function (sessionId, initialSession) {
        initWakeLock();
        coord = new PlaytestEngine.SessionCoordinator(sessionId, initialSession);

        coord.onStatusChange(function (status, msg) {
            var ind = document.getElementById('saveStatusIndicator');
            if (!ind) return;
            ind.dataset.custom = 'true';
            if (status === 'saving') {
                ind.textContent = 'Saving...';
                ind.className = 'text-[10px] text-amber-400 font-mono flex items-center gap-1';
            } else if (status === 'saved') {
                ind.textContent = 'Saved';
                ind.className = 'text-[10px] text-emerald-400 font-mono flex items-center gap-1';
            } else if (status === 'offline') {
                ind.textContent = 'Saved Locally';
                ind.className = 'text-[10px] text-slate-400 font-mono flex items-center gap-1';
            } else if (status === 'conflict') {
                ind.textContent = 'Conflict';
                ind.className = 'text-[10px] text-red-400 font-mono flex items-center gap-1 font-bold';
            }
        });

        // Quick Life Buttons (+1 / -1)
        var btnPlus1 = document.getElementById('btnLifePlus1');
        var btnMinus1 = document.getElementById('btnLifeMinus1');
        if (btnPlus1) {
            btnPlus1.onclick = function () {
                haptic(12);
                coord.dispatch({ type: 'ADJUST_LIFE', payload: { delta: 1 } });
                renderAll();
            };
        }
        if (btnMinus1) {
            btnMinus1.onclick = function () {
                haptic(12);
                coord.dispatch({ type: 'ADJUST_LIFE', payload: { delta: -1 } });
                renderAll();
            };
        }

        // Tap Life counter opens Life sheet
        var hudLifeContainer = document.getElementById('hudLifeContainer');
        if (hudLifeContainer) {
            hudLifeContainer.onclick = function () {
                haptic(15);
                openLifeCounterSheet();
            };
        }

        // Undo Button
        var btnUndo = document.getElementById('btnUndoAction');
        if (btnUndo) {
            btnUndo.onclick = function () {
                haptic(15);
                coord.undo();
                renderAll();
                showToast('Undone.');
            };
        }

        // Close buttons on sheets
        var closeActionSheetBtn = document.getElementById('closeActionSheetBtn');
        if (closeActionSheetBtn) closeActionSheetBtn.onclick = closeActionSheet;

        var closeZoneSheetBtn = document.getElementById('closeZoneSheetBtn');
        if (closeZoneSheetBtn) closeZoneSheetBtn.onclick = function () {
            document.getElementById('zoneInspectionSheet').classList.add('hidden');
        };

        var closeLifeSheetBtn = document.getElementById('closeLifeSheetBtn');
        if (closeLifeSheetBtn) closeLifeSheetBtn.onclick = function () {
            document.getElementById('lifeCounterSheet').classList.add('hidden');
        };

        // Utility Drawer
        var btnOpenUtility = document.getElementById('btnOpenUtility');
        var utilityDrawer = document.getElementById('utilityDrawer');
        var closeUtilityBtn = document.getElementById('closeUtilityBtn');
        if (btnOpenUtility && utilityDrawer) {
            btnOpenUtility.onclick = function () {
                haptic(15);
                utilityDrawer.classList.remove('hidden');
            };
        }
        if (closeUtilityBtn && utilityDrawer) {
            closeUtilityBtn.onclick = function () {
                utilityDrawer.classList.add('hidden');
            };
        }

        // Dice roll & coin flip
        var btnRollD20 = document.getElementById('btnRollD20');
        if (btnRollD20) {
            btnRollD20.onclick = function () {
                haptic(20);
                var roll = Math.floor(Math.random() * 20) + 1;
                showToast('🎲 Rolled D20: ' + roll);
            };
        }
        var btnFlipCoin = document.getElementById('btnFlipCoin');
        if (btnFlipCoin) {
            btnFlipCoin.onclick = function () {
                haptic(20);
                var flip = Math.random() < 0.5 ? 'Heads' : 'Tails';
                showToast('🪙 Coin Flip: ' + flip);
            };
        }

        // Run It Back
        var btnRestartGame = document.getElementById('btnRestartGame');
        if (btnRestartGame) {
            btnRestartGame.onclick = function () {
                if (!confirm('Run it back? This will reshuffle your decks and reset to Turn 1.')) return;
                haptic(20);
                fetch('/api/playtest/' + sessionId + '/restart', { method: 'POST' })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data.success) {
                            window.location.reload();
                        }
                    });
            };
        }

        renderAll();
    };

})(window, document);

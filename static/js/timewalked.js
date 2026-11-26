const _twInit = window.__TIMEWALKED_INIT__ || {};
const INITIAL_DECK_ID = _twInit.initialDeckId ?? null;

let currentTwMode = "text_or_url";
let lastRequestPayload = null;
let currentTwResult = null;
let currentTwBaseline = null;
let currentAsciiReport = "";
let selectedWhatIfExclusions = new Set();
let currentSuggestedUpgrades = null;
let showAllFreeUpgrades = false;
let showAllBuyUpgrades = false;

function getDismissedSuggestions(cmdrKey) {
    try {
        const raw = localStorage.getItem(`tw_dismissed_${cmdrKey}`);
        return raw ? JSON.parse(raw) : [];
    } catch {
        return [];
    }
}

function saveDismissedSuggestion(cmdrKey, cardName) {
    try {
        const cur = getDismissedSuggestions(cmdrKey);
        if (!cur.includes(cardName)) cur.push(cardName);
        localStorage.setItem(`tw_dismissed_${cmdrKey}`, JSON.stringify(cur));
    } catch {}
}

function removeDismissedSuggestion(cmdrKey, cardName) {
    try {
        const cur = getDismissedSuggestions(cmdrKey).filter(n => n !== cardName);
        localStorage.setItem(`tw_dismissed_${cmdrKey}`, JSON.stringify(cur));
    } catch {}
}

function escapeTwHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function switchTimewalkedTab(mode) {
    currentTwMode = mode;
    const modes = ["text_or_url", "vault_deck", "edhrec_commander"];
    modes.forEach(m => {
        const tabBtn = document.getElementById(`tw-tab-${m}`);
        const panel = document.getElementById(`tw-panel-${m}`);
        if (tabBtn) {
            if (m === mode) {
                tabBtn.className = "px-3 py-1.5 bg-[#A855F7] text-white font-bold transition uppercase";
            } else {
                tabBtn.className = "px-3 py-1.5 text-[#94A3B8] hover:text-white transition uppercase";
            }
        }
        if (panel) {
            panel.classList.toggle("hidden", m !== mode);
        }
    });
}

function clearTimewalkedText() {
    document.getElementById("tw-input-deck-name").value = "";
    document.getElementById("tw-input-commander").value = "";
    document.getElementById("tw-input-source").value = "";
}

function analyzeVaultDeck(deckId) {
    selectedWhatIfExclusions.clear();
    const offline = Boolean(document.getElementById("tw-chk-offline")?.checked);
    executeTimewalkedAnalysis({
        mode: "vault_deck",
        deck_id: deckId,
        offline: offline,
        excluded_cards: []
    });
}

function submitTimewalkedTextOrUrl() {
    const source = (document.getElementById("tw-input-source")?.value || "").trim();
    const deckName = (document.getElementById("tw-input-deck-name")?.value || "").trim();
    const commander = (document.getElementById("tw-input-commander")?.value || "").trim();
    const offline = Boolean(document.getElementById("tw-chk-offline")?.checked);

    if (!source) {
        if (typeof showToast === "function") {
            showToast("Please paste a Commander decklist or deck URL first.", "error");
        }
        return;
    }
    selectedWhatIfExclusions.clear();
    executeTimewalkedAnalysis({
        mode: "text_or_url",
        source: source,
        deck_name: deckName,
        commander: commander,
        offline: offline,
        excluded_cards: []
    });
}

function quickProfileCommander(cmdrName) {
    const input = document.getElementById("tw-edhrec-commander-input");
    if (input) input.value = cmdrName;
    submitEdhrecCommanderProfile();
}

function submitEdhrecCommanderProfile() {
    const cmdrName = (document.getElementById("tw-edhrec-commander-input")?.value || "").trim();
    if (!cmdrName) {
        if (typeof showToast === "function") {
            showToast("Please enter a Commander name to profile on EDHREC.", "error");
        }
        return;
    }
    selectedWhatIfExclusions.clear();
    executeTimewalkedAnalysis({
        mode: "edhrec_commander",
        commander_name: cmdrName,
        offline: false,
        excluded_cards: []
    });
}

async function executeTimewalkedAnalysis(payload) {
    lastRequestPayload = { ...payload };
    const loadingBanner = document.getElementById("tw-loading-banner");
    const loadingMsg = document.getElementById("tw-loading-message");
    if (loadingBanner) loadingBanner.classList.remove("hidden");
    if (loadingMsg) {
        if (payload.mode === "edhrec_commander") {
            loadingMsg.textContent = `FETCHING EDHREC AVERAGE DECK & RUNNING TIMEWALKED DIAGNOSTICS FOR ${String(payload.commander_name || "").toUpperCase()}...`;
        } else if (payload.excluded_cards && payload.excluded_cards.length > 0) {
            loadingMsg.textContent = `RECALCULATING WHAT-IF BRACKET SIMULATION (${payload.excluded_cards.length} CARD(S) CUT)...`;
        } else {
            loadingMsg.textContent = "RUNNING TIMEWALKED BRACKET & SPELLBOOK COMBO DIAGNOSTICS...";
        }
    }

    try {
        const resp = await fetch("/api/timewalked/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await resp.json();
        if (!resp.ok || data.error) {
            throw new Error(data.error || `HTTP ${resp.status}`);
        }
        currentTwResult = data.result;
        currentTwBaseline = data.baseline || null;
        currentAsciiReport = data.ascii_report || "";
        renderTimewalkedResults(data);
        document.getElementById("tw-results-section")?.scrollIntoView({ behavior: "smooth", block: "start" });

        // Trigger Timewalked-style Suggested Upgrades scan in background
        loadTimewalkedSuggestedUpgrades(payload);
    } catch (err) {
        if (typeof showToast === "function") {
            showToast(err.message || "Failed to analyze deck.", "error");
        }
    } finally {
        if (loadingBanner) loadingBanner.classList.add("hidden");
    }
}

function getBracketColorTheme(bracket) {
    const b = Number(bracket) || 2;
    if (b <= 1) return { text: "text-[#94A3B8]", border: "border-[#64748B]", bg: "bg-[#64748B]/20", hex: "#94A3B8" };
    if (b === 2) return { text: "text-[#10B981]", border: "border-[#10B981]", bg: "bg-[#10B981]/20", hex: "#10B981" };
    if (b === 3) return { text: "text-[#00CED1]", border: "border-[#00CED1]", bg: "bg-[#00CED1]/20", hex: "#00CED1" };
    if (b === 4) return { text: "text-[#F59E0B]", border: "border-[#F59E0B]", bg: "bg-[#F59E0B]/20", hex: "#F59E0B" };
    return { text: "text-[#FF3358]", border: "border-[#DC143C]", bg: "bg-[#DC143C]/20", hex: "#FF3358" };
}

function renderCardChipHtml(cardName, detailsMap, showWhatIfToggle = false) {
    const info = (detailsMap && detailsMap[cardName]) || {};
    const imgUri = info.image_uri || "";
    const mana = info.mana_cost || "";
    const weight = info.weight ? `+${info.weight.toFixed(2)}` : "";
    const isExcluded = selectedWhatIfExclusions.has(cardName);

    const clickAttr = imgUri
        ? `onclick="openCardImageModal('${escapeTwHtml(imgUri)}', '${escapeTwHtml(cardName)}', 'ANY', '', 'nonfoil')"`
        : "";

    return `
        <span class="inline-flex items-center gap-1.5 px-2 py-0.5 bg-[#10141D] hover:bg-[#222B3D] border ${isExcluded ? 'border-[#DC143C] line-through opacity-60' : 'border-[#263245] hover:border-[#00CED1]'} text-xs font-mono text-white cursor-pointer transition"
              ${clickAttr}
              title="${escapeTwHtml(cardName)}${info.type_line ? ' — ' + escapeTwHtml(info.type_line) : ''}">
            ${imgUri ? `<img src="${escapeTwHtml(typeof toScryfallThumb === 'function' ? toScryfallThumb(imgUri) : imgUri)}" alt="" loading="lazy" decoding="async" class="w-4 h-4 object-cover rounded-sm border border-[#263245]">` : `<span class="w-1.5 h-1.5 rounded-full bg-[#00CED1]"></span>`}
            <span>${escapeTwHtml(cardName)}</span>
            ${weight ? `<span class="text-[10px] text-[#C084FC] font-bold">${escapeTwHtml(weight)}</span>` : ""}
            ${mana ? `<span class="text-[10px] text-[#64748B]">${escapeTwHtml(mana)}</span>` : ""}
        </span>
    `;
}

function renderTimewalkedResults(data) {
    const res = data.result;
    const baseline = data.baseline;
    const details = res.card_details || (baseline && baseline.card_details) || {};

    const resultsSec = document.getElementById("tw-results-section");
    if (resultsSec) resultsSec.classList.remove("hidden");

    // 1. What-If Simulation Banner
    const whatIfBanner = document.getElementById("tw-whatif-banner");
    if (whatIfBanner) {
        if (baseline && data.excluded_cards && data.excluded_cards.length > 0) {
            whatIfBanner.classList.remove("hidden");
            document.getElementById("tw-whatif-cut-count").textContent =
                `Simulated cutting ${data.excluded_cards.length} card(s): ${data.excluded_cards.join(", ")}`;
            const paperDiff = res.on_paper_bracket - baseline.on_paper_bracket;
            const feelsDiff = (res.feels_like_score - baseline.feels_like_score).toFixed(1);
            document.getElementById("tw-whatif-comparison-text").innerHTML = `
                On Paper: <strong class="text-white">Bracket ${baseline.on_paper_bracket} (${escapeTwHtml(baseline.on_paper_label)})</strong> &rarr;
                <strong class="text-[#10B981]">Bracket ${res.on_paper_bracket} (${escapeTwHtml(res.on_paper_label)})</strong> (${paperDiff <= 0 ? paperDiff : '+' + paperDiff})
                &nbsp;|&nbsp;
                Feels Like: <strong class="text-white">${baseline.feels_like_score.toFixed(1)}</strong> &rarr;
                <strong class="text-[#00CED1]">${res.feels_like_score.toFixed(1)}</strong> (${feelsDiff <= 0 ? feelsDiff : '+' + feelsDiff})
                &nbsp;|&nbsp;
                Est. Win Turn: <strong class="text-white">T${baseline.estimated_win_turn}</strong> &rarr; <strong class="text-[#10B981]">T${res.estimated_win_turn}</strong>
            `;
        } else {
            whatIfBanner.classList.add("hidden");
        }
    }

    // 2. Header identity
    document.getElementById("tw-res-deck-name").textContent = res.deck_name || "Commander Deck";
    document.getElementById("tw-res-card-count-badge").textContent = `${res.total_cards} cards (${res.land_count || 0} lands)`;
    document.getElementById("tw-res-cmc-badge").textContent = `Avg Non-Land MV ${Number(res.avg_cmc_nonland || 0).toFixed(2)}`;
    const cmdrStr = (res.commanders && res.commanders.length > 0) ? res.commanders.join(" // ") : "Unspecified Commander";
    document.getElementById("tw-res-commanders").textContent = `Commander: ${cmdrStr}`;

    // Commander Art
    const artBox = document.getElementById("tw-cmdr-art-box");
    const firstCmdr = res.commanders && res.commanders[0];
    const cmdrImg = firstCmdr && details[firstCmdr] && details[firstCmdr].image_uri;
    if (artBox) {
        if (cmdrImg) {
            artBox.innerHTML = `<img src="${escapeTwHtml(cmdrImg)}" alt="${escapeTwHtml(firstCmdr)}" class="w-full h-full object-cover cursor-pointer" onclick="openCardImageModal('${escapeTwHtml(cmdrImg)}', '${escapeTwHtml(firstCmdr)}', 'ANY', '', 'nonfoil')">`;
        } else {
            artBox.innerHTML = `<span class="text-xs font-mono font-bold text-[#C084FC]">EDH</span>`;
        }
    }

    // 3. On Paper Hero Card
    const paperTheme = getBracketColorTheme(res.on_paper_bracket);
    const paperNumEl = document.getElementById("tw-res-paper-bracket-num");
    const paperLblEl = document.getElementById("tw-res-paper-bracket-label");
    paperNumEl.textContent = `Bracket ${res.on_paper_bracket}`;
    paperNumEl.className = `text-4xl sm:text-5xl font-black font-heading ${paperTheme.text}`;
    paperLblEl.textContent = `· ${res.on_paper_label}`;

    const tierBar = document.getElementById("tw-paper-tier-bar");
    if (tierBar) {
        tierBar.querySelectorAll("[data-tier]").forEach(el => {
            const t = Number(el.getAttribute("data-tier"));
            if (t === res.on_paper_bracket) {
                el.className = `p-1.5 text-center border-2 ${paperTheme.border} ${paperTheme.bg} ${paperTheme.text} font-bold`;
            } else {
                el.className = "p-1.5 text-center border border-[#263245] bg-[#10141D] text-[#64748B]";
            }
        });
    }

    const reasonsList = document.getElementById("tw-res-paper-reasons");
    if (reasonsList) {
        reasonsList.innerHTML = (res.on_paper_reasons || []).map(r => `
            <li class="flex items-start space-x-2 bg-[#10141D] p-2.5 border border-[#263245]">
                <span class="${paperTheme.text} font-bold">&bull;</span>
                <span>${escapeTwHtml(r)}</span>
            </li>
        `).join("");
    }

    // 4. Feels Like Hero Card
    const feelsScore = Number(res.feels_like_score || 2.0);
    const feelsTheme = getBracketColorTheme(Math.round(feelsScore));
    const feelsEl = document.getElementById("tw-res-feels-score");
    feelsEl.textContent = feelsScore.toFixed(1);
    feelsEl.className = `text-4xl sm:text-5xl font-black font-heading ${feelsTheme.text}`;

    document.getElementById("tw-res-win-turn").textContent = `~Turn ${res.estimated_win_turn}`;
    document.getElementById("tw-res-verdict").textContent = res.summary_verdict || "";

    const delta = Number((feelsScore - res.on_paper_bracket).toFixed(1));
    const deltaBadge = document.getElementById("tw-res-delta-badge");
    if (delta >= 0.5) {
        deltaBadge.textContent = `+${delta.toFixed(1)} Punches Above Paper`;
        deltaBadge.className = "text-[10px] font-mono px-2.5 py-0.5 font-bold uppercase border border-[#F59E0B] bg-[#F59E0B]/20 text-[#F59E0B]";
    } else if (delta <= -0.5) {
        deltaBadge.textContent = `${delta.toFixed(1)} Softer Than Paper`;
        deltaBadge.className = "text-[10px] font-mono px-2.5 py-0.5 font-bold uppercase border border-[#10B981] bg-[#10B981]/20 text-[#10B981]";
    } else {
        deltaBadge.textContent = `Plays True to Bracket ${res.on_paper_bracket}`;
        deltaBadge.className = "text-[10px] font-mono px-2.5 py-0.5 font-bold uppercase border border-[#00CED1] bg-[#00CED1]/20 text-[#00CED1]";
    }

    // Gauge percentage (1.0 -> 5%, 5.0 -> 100%)
    const pct = Math.max(5, Math.min(100, ((feelsScore - 1.0) / 4.0) * 100));
    const gaugeFill = document.getElementById("tw-feels-gauge-fill");
    if (gaugeFill) gaugeFill.style.width = `${pct}%`;

    const netMod = (feelsScore - 2.0).toFixed(2);
    document.getElementById("tw-res-net-modifier").textContent =
        `${Number(netMod) >= 0 ? '+' : ''}${netMod} Net Modifier`;

    // 5. Itemized Receipts Ledger
    const receiptsContainer = document.getElementById("tw-receipts-list");
    if (receiptsContainer) {
        const receipts = res.receipts || [];
        if (receipts.length === 0) {
            receiptsContainer.innerHTML = `
                <div class="p-3 bg-[#10141D] border border-[#263245] font-mono text-xs text-[#94A3B8]">
                    +0.00 &middot; Standard Core Commander deck construction (Base 2.00)
                </div>
            `;
        } else {
            receiptsContainer.innerHTML = receipts.map(r => {
                const isPos = r.delta >= 0;
                const sign = isPos ? "+" : "";
                const badgeCls = isPos
                    ? "bg-[#00CED1]/15 text-[#00CED1] border-[#00CED1]/50"
                    : "bg-[#10B981]/15 text-[#10B981] border-[#10B981]/50";
                const barWidth = Math.min(100, Math.round((Math.abs(r.delta) / 1.35) * 100));
                const cardsHtml = (r.cards || []).slice(0, 12).map(c => renderCardChipHtml(c, details)).join("");
                const extraCount = (r.cards || []).length > 12 ? `<span class="text-[11px] font-mono text-[#64748B]">+${r.cards.length - 12} more</span>` : "";

                return `
                    <div class="bg-[#10141D] border border-[#263245] p-3.5 space-y-2">
                        <div class="flex flex-wrap items-center justify-between gap-2">
                            <div class="flex items-center space-x-2.5">
                                <span class="font-mono font-bold text-xs px-2 py-0.5 border ${badgeCls}">${sign}${Number(r.delta).toFixed(2)}</span>
                                <span class="text-[10px] font-mono uppercase px-2 py-0.5 bg-[#1B2230] border border-[#263245] text-[#C084FC] font-bold">${escapeTwHtml(r.category.replace(/_/g, " "))}</span>
                                <span class="text-xs font-mono text-white font-semibold">${escapeTwHtml(r.explanation)}</span>
                            </div>
                        </div>
                        <div class="w-full h-1 bg-[#1B2230] overflow-hidden">
                            <div class="h-full ${isPos ? 'bg-[#00CED1]' : 'bg-[#10B981]'}" style="width: ${barWidth}%;"></div>
                        </div>
                        ${cardsHtml ? `<div class="flex flex-wrap items-center gap-1.5 pt-1">${cardsHtml}${extraCount}</div>` : ""}
                    </div>
                `;
            }).join("");
        }
    }

    // 6. Interactive What-If Tuner Candidates
    renderWhatIfTunerCandidates(baseline || res, details);

    // 7. Official Game Changers Panel
    const gcList = res.game_changers || [];
    const gcBadge = document.getElementById("tw-gc-count-badge");
    const gcGrid = document.getElementById("tw-gc-grid");
    if (gcBadge) {
        gcBadge.textContent = `${gcList.length} Game Changer${gcList.length === 1 ? '' : 's'}`;
        gcBadge.className = gcList.length >= 4
            ? "text-xs font-mono font-bold px-2.5 py-0.5 bg-[#DC143C]/20 text-[#FF3358] border border-[#DC143C]/50"
            : (gcList.length >= 1
                ? "text-xs font-mono font-bold px-2.5 py-0.5 bg-[#A855F7]/20 text-[#C084FC] border border-[#A855F7]/50"
                : "text-xs font-mono font-bold px-2.5 py-0.5 bg-[#10B981]/20 text-[#10B981] border border-[#10B981]/50");
    }
    if (gcGrid) {
        if (gcList.length === 0) {
            gcGrid.innerHTML = `<div class="col-span-2 p-4 bg-[#10141D] border border-[#263245] text-xs font-mono text-[#10B981]">✓ 0 Official WotC Game Changers in this list (Bracket 1 &amp; 2 eligible).</div>`;
        } else {
            gcGrid.innerHTML = gcList.map(name => {
                const c = details[name] || {};
                const img = c.image_uri || "";
                const weight = c.weight ? `+${c.weight.toFixed(2)}` : "";
                return `
                    <div class="p-2.5 bg-[#10141D] border border-[#A855F7]/40 hover:border-[#A855F7] flex items-center justify-between transition cursor-pointer"
                         ${img ? `onclick="openCardImageModal('${escapeTwHtml(img)}', '${escapeTwHtml(name)}', 'ANY', '', 'nonfoil')"` : ""}>
                        <div class="flex items-center space-x-2.5 min-w-0">
                            ${img ? `<img src="${escapeTwHtml(typeof toScryfallThumb === 'function' ? toScryfallThumb(img) : img)}" alt="" loading="lazy" decoding="async" class="w-8 h-11 object-cover border border-[#263245] flex-shrink-0">` : `<div class="w-8 h-11 bg-[#1B2230] border border-[#263245] flex items-center justify-center text-[9px] font-mono text-[#C084FC]">GC</div>`}
                            <div class="min-w-0">
                                <div class="text-xs font-mono font-bold text-white truncate">${escapeTwHtml(name)}</div>
                                <div class="text-[10px] font-mono text-[#94A3B8] truncate">${escapeTwHtml(c.type_line || "Game Changer")}</div>
                            </div>
                        </div>
                        <span class="text-xs font-mono font-bold text-[#C084FC] ml-2 flex-shrink-0">${escapeTwHtml(weight)}</span>
                    </div>
                `;
            }).join("");
        }
    }

    // 8. Detected Combos Panel
    const combos = res.combos || [];
    const combosBadge = document.getElementById("tw-combos-count-badge");
    const almostBadge = document.getElementById("tw-almost-combos-badge");
    const combosListEl = document.getElementById("tw-combos-list");
    const comboSourceEl = document.getElementById("tw-combo-source-label");

    if (combosBadge) {
        combosBadge.textContent = `${combos.length} Combo${combos.length === 1 ? '' : 's'}`;
    }
    if (almostBadge) {
        if (res.almost_included_combos_count > 0) {
            almostBadge.textContent = `${res.almost_included_combos_count} Near-Miss`;
            almostBadge.classList.remove("hidden");
        } else {
            almostBadge.classList.add("hidden");
        }
    }
    if (comboSourceEl) {
        comboSourceEl.textContent = res.combo_source === "commander_spellbook_api" ? "Live Spellbook API" : "Local Combo DB";
    }
    if (combosListEl) {
        if (combos.length === 0) {
            combosListEl.innerHTML = `<div class="p-4 bg-[#10141D] border border-[#263245] text-xs font-mono text-[#94A3B8]">No 2-card or deterministic infinite combos detected.</div>`;
        } else {
            combosListEl.innerHTML = combos.map(c => {
                const cardsArr = c.cards || [];
                const producesArr = c.produces || [];
                const bTag = c.bracket_tag || "3";
                const bTheme = getBracketColorTheme(bTag);
                return `
                    <div class="p-3 bg-[#10141D] border border-[#263245] space-y-1.5">
                        <div class="flex items-center justify-between gap-2">
                            <div class="flex flex-wrap items-center gap-1.5">
                                ${cardsArr.map(cn => renderCardChipHtml(cn, details)).join('<span class="text-xs font-mono text-[#F59E0B] font-bold">+</span>')}
                            </div>
                            <span class="text-[10px] font-mono px-2 py-0.5 border ${bTheme.border} ${bTheme.bg} ${bTheme.text} font-bold uppercase flex-shrink-0">
                                Combo B${escapeTwHtml(bTag)}
                            </span>
                        </div>
                        <div class="text-[11px] font-mono text-[#94A3B8]">
                            <span class="text-[#F59E0B] font-bold">Produces:</span> ${escapeTwHtml(producesArr.join(", ") || "Combo synergy")}
                            ${c.mana_needed ? ` &bull; <span class="text-[#00CED1]">Mana: ${escapeTwHtml(c.mana_needed)}</span>` : ""}
                            ${c.min_turn ? ` &bull; <span class="text-[#10B981]">Est. Turn ${escapeTwHtml(c.min_turn)}+</span>` : ""}
                        </div>
                    </div>
                `;
            }).join("");
        }
    }

    // 9. Functional Packages Breakdown
    renderPackagesGrid(res, details);

    // 10. EDHREC Meta Profile Panel
    const edhrecPanel = document.getElementById("tw-edhrec-profile-panel");
    if (edhrecPanel) {
        if (data.edhrec_profile && data.edhrec_profile.top_cards && data.edhrec_profile.top_cards.length > 0) {
            edhrecPanel.classList.remove("hidden");
            document.getElementById("tw-edhrec-title").textContent =
                `EDHREC Community Profile // ${data.edhrec_profile.commander}`;
            document.getElementById("tw-edhrec-total-decks").textContent =
                `${Number(data.edhrec_profile.total_decks || 0).toLocaleString()} Recorded Decks`;
            document.getElementById("tw-edhrec-top-cards").innerHTML = data.edhrec_profile.top_cards.slice(0, 16).map(tc => `
                <div class="p-2.5 bg-[#10141D] border border-[#263245] flex items-center justify-between">
                    <div class="min-w-0 pr-2">
                        <div class="text-xs font-mono font-bold text-white truncate">${escapeTwHtml(tc.name)}</div>
                        <div class="text-[10px] font-mono text-[#64748B] truncate">${escapeTwHtml(tc.category)}</div>
                    </div>
                    <div class="text-right font-mono flex-shrink-0">
                        <div class="text-xs font-bold text-[#00CED1]">${Number(tc.inclusion_rate || 0).toFixed(1)}%</div>
                        <div class="text-[9px] text-[#94A3B8]">Inclusion</div>
                    </div>
                </div>
            `).join("");
        } else {
            edhrecPanel.classList.add("hidden");
        }
    }

    // 11. ASCII Report Pre
    const asciiPre = document.getElementById("tw-ascii-pre");
    if (asciiPre) asciiPre.textContent = currentAsciiReport;
}

function renderWhatIfTunerCandidates(sourceRes, details) {
    const container = document.getElementById("tw-whatif-candidates");
    if (!container) return;

    const pkgs = sourceRes.packages || {};
    const comboCards = [];
    (sourceRes.combos || []).forEach(c => {
        (c.cards || []).forEach(cn => {
            if (!comboCards.includes(cn)) comboCards.push(cn);
        });
    });

    const candidateSet = new Set([
        ...(sourceRes.game_changers || []),
        ...(pkgs.mass_land_denial || []),
        ...(pkgs.extra_turn || []),
        ...comboCards,
        ...(pkgs.fast_mana || []),
        ...(pkgs.free_interaction || []),
        ...(pkgs.finisher || []),
        ...(pkgs.strong_card || []),
    ]);

    // Remove commanders from cut candidates
    const cmdrsLower = new Set((sourceRes.commanders || []).map(c => c.toLowerCase()));
    const candidates = Array.from(candidateSet).filter(c => !cmdrsLower.has(c.toLowerCase()));

    if (candidates.length === 0) {
        container.innerHTML = `<span class="text-xs font-mono text-[#64748B]">No high-impact Game Changers, combos, or power staples flagged for tuning.</span>`;
        return;
    }

    container.innerHTML = candidates.map(name => {
        const isCut = selectedWhatIfExclusions.has(name);
        const info = details[name] || {};
        const isGc = info.game_changer;
        const badge = isGc ? "GC" : (comboCards.includes(name) ? "COMBO" : (info.weight ? `+${info.weight.toFixed(2)}` : "ROLE"));
        return `
            <button type="button" onclick="toggleWhatIfCard(${ JSON.stringify(name).replace(/"/g, '&quot;') })"
                class="px-2.5 py-1.5 text-xs font-mono border transition flex items-center space-x-1.5 ${
                    isCut
                        ? 'bg-[#DC143C]/20 border-[#DC143C] text-[#FF3358] line-through'
                        : 'bg-[#10141D] border-[#263245] hover:border-[#10B981] text-white'
                }">
                <span>${isCut ? '✕' : '✓'}</span>
                <span>${escapeTwHtml(name)}</span>
                <span class="text-[9px] px-1 py-0.2 bg-[#1B2230] text-[#C084FC] font-bold">${escapeTwHtml(badge)}</span>
            </button>
        `;
    }).join("");
}

function toggleWhatIfCard(cardName) {
    if (selectedWhatIfExclusions.has(cardName)) {
        selectedWhatIfExclusions.delete(cardName);
    } else {
        selectedWhatIfExclusions.add(cardName);
    }
    const base = currentTwBaseline || currentTwResult;
    if (base) {
        renderWhatIfTunerCandidates(base, base.card_details || {});
    }
}

function applyWhatIfSimulation() {
    if (!lastRequestPayload) return;
    executeTimewalkedAnalysis({
        ...lastRequestPayload,
        excluded_cards: Array.from(selectedWhatIfExclusions)
    });
}

function resetWhatIfExclusions() {
    selectedWhatIfExclusions.clear();
    if (!lastRequestPayload) return;
    executeTimewalkedAnalysis({
        ...lastRequestPayload,
        excluded_cards: []
    });
}

function renderPackagesGrid(res, details) {
    const grid = document.getElementById("tw-packages-grid");
    if (!grid) return;

    const pkgs = res.packages || {};
    const counts = res.package_counts || {};

    const packageDefs = [
        { key: "fast_mana", label: "Fast Mana (0–1 MV / Rituals)", color: "text-[#FF3358]", border: "border-[#DC143C]/40" },
        { key: "ramp", label: "Mana Ramp & Acceleration", color: "text-[#10B981]", border: "border-[#10B981]/40" },
        { key: "tutor", label: "Tutors & Library Search", color: "text-[#C084FC]", border: "border-[#A855F7]/40" },
        { key: "card_draw", label: "Card Draw & Advantage", color: "text-[#00CED1]", border: "border-[#00CED1]/40" },
        { key: "interaction", label: "Targeted Interaction & Countermagic", color: "text-[#60A5FA]", border: "border-[#3B82F6]/40" },
        { key: "free_interaction", label: "Zero-Mana / Free Interaction", color: "text-[#F59E0B]", border: "border-[#F59E0B]/40" },
        { key: "finisher", label: "Finishers & Closing Threats", color: "text-[#FF3358]", border: "border-[#DC143C]/40" },
        { key: "extra_turn", label: "Extra Turn Spells", color: "text-[#C084FC]", border: "border-[#A855F7]/40" },
        { key: "mass_land_denial", label: "Mass Land Denial / Stax Locks", color: "text-[#FF3358]", border: "border-[#DC143C]/40" },
    ];

    grid.innerHTML = packageDefs.map(def => {
        const list = pkgs[def.key] || [];
        const cnt = counts[def.key] !== undefined ? counts[def.key] : list.length;
        const cardsHtml = list.length > 0
            ? list.map(c => renderCardChipHtml(c, details)).join("")
            : `<span class="text-[11px] font-mono text-[#64748B]">None detected</span>`;

        return `
            <div class="bg-[#10141D] border ${cnt > 0 ? def.border : 'border-[#263245]'} p-3.5 flex flex-col justify-between space-y-2.5">
                <div class="flex items-center justify-between border-b border-[#263245] pb-2">
                    <span class="text-xs font-mono font-bold text-white uppercase">${escapeTwHtml(def.label)}</span>
                    <span class="text-xs font-mono font-bold ${cnt > 0 ? def.color : 'text-[#64748B]'} px-2 py-0.5 bg-[#1B2230] border border-[#263245]">${cnt}</span>
                </div>
                <div class="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto pr-1">
                    ${cardsHtml}
                </div>
            </div>
        `;
    }).join("");
}

async function runFleetBracketScan() {
    const btn = document.getElementById("btn-tw-fleet-scan");
    const container = document.getElementById("tw-fleet-matrix-container");
    const tbody = document.getElementById("tw-fleet-matrix-tbody");
    const summaryBadge = document.getElementById("tw-fleet-summary-badge");
    const offline = Boolean(document.getElementById("tw-chk-offline")?.checked);

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span>Scanning Fleet...</span>`;
    }

    try {
        const resp = await fetch("/api/timewalked/fleet", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ offline: offline })
        });
        const data = await resp.json();
        if (!resp.ok || data.error) {
            throw new Error(data.error || `HTTP ${resp.status}`);
        }

        if (container) container.classList.remove("hidden");
        if (summaryBadge) summaryBadge.textContent = `${data.total_scanned} Decks Classified`;

        if (tbody) {
            tbody.innerHTML = (data.fleet || []).map(item => {
                const pTheme = getBracketColorTheme(item.on_paper_bracket);
                const fTheme = getBracketColorTheme(Math.round(item.feels_like_score));
                const dSign = item.delta >= 0 ? "+" : "";
                return `
                    <tr class="hover:bg-[#10141D] transition">
                        <td class="p-2.5">
                            <div class="font-bold text-white">${escapeTwHtml(item.deck_name)}</div>
                            <div class="text-[10px] text-[#94A3B8]">${escapeTwHtml(item.commander_name)}</div>
                        </td>
                        <td class="p-2.5 text-center">
                            <span class="px-2 py-0.5 border ${pTheme.border} ${pTheme.bg} ${pTheme.text} font-bold text-[11px]">
                                B${item.on_paper_bracket} &middot; ${escapeTwHtml(item.on_paper_label)}
                            </span>
                        </td>
                        <td class="p-2.5 text-center font-bold text-sm ${fTheme.text}">
                            ${Number(item.feels_like_score).toFixed(1)}
                        </td>
                        <td class="p-2.5 text-center text-[11px] ${item.delta >= 0.5 ? 'text-[#F59E0B] font-bold' : (item.delta <= -0.5 ? 'text-[#10B981]' : 'text-[#94A3B8]')}">
                            ${dSign}${Number(item.delta).toFixed(1)}
                        </td>
                        <td class="p-2.5 text-center text-[#10B981] font-bold">
                            ~T${item.estimated_win_turn}
                        </td>
                        <td class="p-2.5 text-center text-[#C084FC] font-bold">
                            ${item.game_changers_count}
                        </td>
                        <td class="p-2.5 text-center text-[#F59E0B] font-bold">
                            ${item.combos_count}
                        </td>
                        <td class="p-2.5 text-right">
                            <button type="button" onclick="analyzeVaultDeck(${item.deck_id})" class="px-2.5 py-1 bg-[#10141D] hover:bg-[#A855F7] text-[#00CED1] hover:text-white border border-[#263245] text-[10px] uppercase font-bold transition">
                                Receipts &rarr;
                            </button>
                        </td>
                    </tr>
                `;
            }).join("");
        }
    } catch (err) {
        if (typeof showToast === "function") {
            showToast(err.message || "Fleet scan failed.", "error");
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `<span>Scan Entire Fleet (${_twInit.deckCount ?? 0} Decks)</span>`;
        }
    }
}

function copyRuleZeroPitch() {
    if (!currentTwResult) return;
    const r = currentTwResult;
    const cmdr = (r.commanders && r.commanders.length) ? r.commanders.join(" // ") : "Commander";
    const gcStr = (r.game_changers && r.game_changers.length)
        ? `Game Changers (${r.game_changers.length}): ${r.game_changers.join(", ")}`
        : "0 Game Changers";
    const comboStr = (r.combos && r.combos.length)
        ? `${r.combos.length} combo line(s) (${r.combos.slice(0, 2).map(c => (c.cards || []).join(" + ")).join("; ")})`
        : "No 2-card infinite combos";

    const pitch = [
        `🛡️ **${r.deck_name}** (${cmdr})`,
        `• **On Paper:** Bracket ${r.on_paper_bracket} (${r.on_paper_label})`,
        `• **Feels Like:** ${Number(r.feels_like_score).toFixed(1)} / 5.0 (Est. Win ~Turn ${r.estimated_win_turn})`,
        `• **Verdict:** ${r.summary_verdict}`,
        `• **Key Signals:** ${gcStr} | ${comboStr}`
    ].join("\n");

    navigator.clipboard.writeText(pitch).then(() => {
        if (typeof showToast === "function") {
            showToast("Copied Rule Zero pre-game pitch to clipboard!", "success");
        }
    });
}

function copyAsciiReceipt() {
    if (!currentAsciiReport) return;
    navigator.clipboard.writeText(currentAsciiReport).then(() => {
        if (typeof showToast === "function") {
            showToast("Copied full Timewalked receipt report to clipboard!", "success");
        }
    });
}

function toggleAsciiModal() {
    const el = document.getElementById("tw-ascii-modal");
    if (el) el.classList.toggle("hidden");
}

function attachCommanderAutocomplete(inputId, dropdownId) {
    const input = document.getElementById(inputId);
    const dropdown = document.getElementById(dropdownId);
    if (!input || !dropdown) return;

    let timer = null;
    input.addEventListener("input", () => {
        const q = input.value.trim();
        clearTimeout(timer);
        if (q.length < 2) {
            dropdown.classList.add("hidden");
            return;
        }
        timer = setTimeout(async () => {
            try {
                const resp = await fetch(`/api/scryfall/autocomplete?q=${encodeURIComponent(q)}`);
                const data = await resp.json();
                const list = data.suggestions || [];
                if (list.length === 0) {
                    dropdown.classList.add("hidden");
                    return;
                }
                dropdown.innerHTML = list.slice(0, 8).map(name => `
                    <div class="px-3 py-2 hover:bg-[#222B3D] text-white cursor-pointer" data-val="${escapeTwHtml(name)}">
                        ${escapeTwHtml(name)}
                    </div>
                `).join("");
                dropdown.classList.remove("hidden");
                dropdown.querySelectorAll("[data-val]").forEach(row => {
                    row.addEventListener("click", () => {
                        input.value = row.getAttribute("data-val");
                        dropdown.classList.add("hidden");
                    });
                });
            } catch {
                dropdown.classList.add("hidden");
            }
        }, 220);
    });

    document.addEventListener("click", (e) => {
        if (!input.contains(e.target) && !dropdown.contains(e.target)) {
            dropdown.classList.add("hidden");
        }
    });
}

document.addEventListener("DOMContentLoaded", () => {
    attachCommanderAutocomplete("tw-input-commander", "tw-cmdr-autocomplete");
    attachCommanderAutocomplete("tw-edhrec-commander-input", "tw-edhrec-autocomplete");

    if (INITIAL_DECK_ID) {
        switchTimewalkedTab("vault_deck");
        analyzeVaultDeck(INITIAL_DECK_ID);
    }
});

// -----------------------------------------------------------------------------
// TIMEWALKED SUGGESTED UPGRADES & BINDER MATCHING CLIENT LOGIC
// -----------------------------------------------------------------------------

async function loadTimewalkedSuggestedUpgrades(basePayload) {
    const loadingEl = document.getElementById("tw-upgrades-loading");
    const container = document.getElementById("tw-upgrades-container");
    if (loadingEl) loadingEl.classList.remove("hidden");
    if (container) container.classList.add("hidden");

    const cmdrTitle = document.getElementById("tw-sug-cmdr-title");
    const primaryCmdr = (currentTwResult && currentTwResult.commanders && currentTwResult.commanders[0]) || "COMMANDER";
    if (cmdrTitle) cmdrTitle.textContent = primaryCmdr.toUpperCase();

    const cmdrKey = primaryCmdr.toLowerCase().replace(/[^a-z0-9]+/g, "");
    const dismissed = getDismissedSuggestions(cmdrKey);

    try {
        const resp = await fetch("/api/timewalked/suggested-upgrades", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                ...basePayload,
                dismissed: dismissed,
                limit: 30
            })
        });
        const json = await resp.json();
        if (!resp.ok || json.error) {
            throw new Error(json.error || "Failed to load upgrades");
        }
        currentSuggestedUpgrades = json.data;
        renderTimewalkedSuggestedUpgrades(json.data);
    } catch (err) {
        console.warn("Suggested upgrades error:", err);
        if (loadingEl) {
            loadingEl.innerHTML = `<span class="text-xs font-mono text-[#64748B]">Could not load EDHREC recommendations (${escapeTwHtml(err.message)}).</span>`;
        }
    } finally {
        if (loadingEl && currentSuggestedUpgrades) loadingEl.classList.add("hidden");
        if (container && currentSuggestedUpgrades) container.classList.remove("hidden");
    }
}

function renderTimewalkedSuggestedUpgrades(data) {
    if (!data) return;

    // 1. Render Ratio Breakdown Strip
    const ratioChipsEl = document.getElementById("tw-ratio-chips");
    const ratios = data.ratios || {};
    let metCount = 0;
    let totalTargets = 0;

    if (ratioChipsEl) {
        ratioChipsEl.innerHTML = Object.values(ratios).map(r => {
            totalTargets++;
            const state = r.state;
            if (state === "met") metCount++;
            let badgeCls = "bg-[#10B981]/15 text-[#10B981] border-[#10B981]/40";
            let dotCls = "bg-[#10B981]";
            if (state === "close") {
                badgeCls = "bg-[#F59E0B]/15 text-[#F59E0B] border-[#F59E0B]/40";
                dotCls = "bg-[#F59E0B]";
            } else if (state === "short") {
                badgeCls = "bg-[#DC143C]/15 text-[#FF3358] border-[#DC143C]/40";
                dotCls = "bg-[#FF3358]";
            }

            return `
                <div class="p-2 bg-[#1B2230] border border-[#263245] flex flex-col justify-between" title="${r.label}: ${r.count} of ${r.target} target">
                    <div class="flex items-center justify-between text-[10px] font-mono mb-1">
                        <span class="text-white truncate font-bold">${escapeTwHtml(r.label)}</span>
                        <span class="w-1.5 h-1.5 rounded-full ${dotCls}"></span>
                    </div>
                    <div class="flex items-baseline justify-between font-mono">
                        <span class="text-xs font-bold text-white">${r.count}</span>
                        <span class="text-[10px] text-[#64748B]">/ ${r.target}</span>
                    </div>
                    <div class="mt-1 text-[9px] font-mono px-1 py-0.2 border text-center font-bold uppercase ${badgeCls}">
                        ${state ? state.toUpperCase() : "OK"}
                    </div>
                </div>
            `;
        }).join("");
    }

    const ratioSummary = document.getElementById("tw-ratio-summary-badge");
    if (ratioSummary) {
        ratioSummary.textContent = `${metCount} OF ${totalTargets} CATEGORIES AT TARGET`;
    }

    // 2. Render Free Upgrades (Binder)
    const freeBadge = document.getElementById("tw-free-badge");
    if (freeBadge) {
        freeBadge.textContent = `${data.free_count || 0} Cards · $${Number(data.free_value_usd || 0).toFixed(2)} Value`;
    }
    renderUpgradeColumn("free", data.free_upgrades || [], showAllFreeUpgrades);

    // 3. Render Buy List
    const buyBadge = document.getElementById("tw-buy-badge");
    if (buyBadge) {
        buyBadge.textContent = `${data.buy_count || 0} Cards · $${Number(data.buy_total_usd || 0).toFixed(2)} Total`;
    }
    renderUpgradeColumn("buy", data.buy_upgrades || [], showAllBuyUpgrades);
}

function renderUpgradeColumn(type, list, showAll) {
    const listEl = document.getElementById(type === "free" ? "tw-free-cards-list" : "tw-buy-cards-list");
    const moreBtn = document.getElementById(type === "free" ? "btn-tw-more-free" : "btn-tw-more-buy");
    if (!listEl) return;

    if (!list || list.length === 0) {
        listEl.innerHTML = `
            <div class="p-6 bg-[#10141D] border border-[#263245] text-center text-xs font-mono text-[#64748B]">
                ${type === "free" ? "No free binder upgrades found for this commander. Check Buy List for popular additions." : "No market upgrades pending."}
            </div>
        `;
        if (moreBtn) moreBtn.classList.add("hidden");
        return;
    }

    const displayList = showAll ? list : list.slice(0, 6);
    if (moreBtn) {
        if (list.length > 6) {
            moreBtn.classList.remove("hidden");
            moreBtn.textContent = showAll ? `▲ Show Fewer (Top 6)` : `▼ Show All (${list.length} Suggestions)`;
        } else {
            moreBtn.classList.add("hidden");
        }
    }

    listEl.innerHTML = displayList.map(item => renderUpgradeTileHtml(item, type)).join("");
}

function renderUpgradeTileHtml(item, type) {
    const isFree = type === "free";
    const imgUri = item.image_uri || "";
    const primaryCmdr = (currentTwResult && currentTwResult.commanders && currentTwResult.commanders[0]) || "";
    const cmdrKey = primaryCmdr.toLowerCase().replace(/[^a-z0-9]+/g, "");
    const deckId = currentSuggestedUpgrades && currentSuggestedUpgrades.deck_id;

    // Reason chips
    const reasonChipsHtml = (item.reasons || []).map(r => `
        <span class="inline-block text-[10px] font-mono px-1.5 py-0.5 bg-[#10141D] border border-[#263245] text-[#94A3B8]">
            ${escapeTwHtml(r)}
        </span>
    `).join("");

    // Binder badge
    let binderBadge = "";
    if (isFree && item.binders && item.binders.length > 0) {
        binderBadge = `
            <span class="text-[10px] font-mono px-2 py-0.5 bg-[#10B981]/20 text-[#10B981] border border-[#10B981]/40 font-bold" title="Found in binder: ${item.binders.join(', ')}">
                📖 ${escapeTwHtml(item.binders.join(', '))} (${item.owned_qty}x)
            </span>
        `;
    } else if (isFree) {
        binderBadge = `
            <span class="text-[10px] font-mono px-2 py-0.5 bg-[#10B981]/20 text-[#10B981] border border-[#10B981]/40 font-bold">
                Owned (${item.owned_qty}x)
            </span>
        `;
    }

    // Other deck allocation note
    let allocBadge = "";
    if (item.in_other_decks && item.in_other_decks.length > 0) {
        allocBadge = `
            <span class="text-[10px] font-mono px-1.5 py-0.2 bg-[#F59E0B]/15 text-[#F59E0B] border border-[#F59E0B]/30" title="Also slotted in: ${item.in_other_decks.join(', ')}">
                Also in: ${escapeTwHtml(item.in_other_decks[0])}${item.in_other_decks.length > 1 ? ` (+${item.in_other_decks.length - 1})` : ''}
            </span>
        `;
    }

    // Bracket warning badge
    let bracketWarnHtml = "";
    if (item.bracket_warning) {
        bracketWarnHtml = `
            <div class="text-[10px] font-mono text-[#F59E0B] bg-[#F59E0B]/10 border border-[#F59E0B]/40 px-2 py-1 flex items-center space-x-1">
                <span>⚠️</span>
                <span>${escapeTwHtml(item.bracket_warning)}</span>
            </div>
        `;
    }

    // Cut Pairing Banner
    let cutHtml = "";
    if (item.card_out) {
        cutHtml = `
            <div class="bg-[#10141D] p-2 border border-[#263245] flex items-center justify-between text-xs font-mono">
                <div class="flex items-center space-x-2 truncate">
                    <span class="text-[#FF3358] font-bold">✂ CUT:</span>
                    <strong class="text-white truncate">${escapeTwHtml(item.card_out)}</strong>
                    <span class="text-[10px] text-[#64748B] hidden sm:inline">(${escapeTwHtml(item.cut_reason)})</span>
                </div>
                ${deckId ? `
                    <button type="button" onclick="applyTimewalkedSwap(this, ${deckId}, '${escapeTwHtml(item.card_out)}', '${escapeTwHtml(item.card_in)}')" class="btn-crimson px-2.5 py-1 text-[10px] font-mono font-bold uppercase ml-2 flex-shrink-0">
                        Swap Now
                    </button>
                ` : ''}
            </div>
        `;
    }

    return `
        <div class="bg-[#10141D] border border-[#263245] hover:border-[#3D4F6B] p-3.5 space-y-2.5 transition" id="tw-tile-${escapeTwHtml(item.card_in.replace(/[^a-zA-Z0-9]/g, ''))}">
            <div class="flex items-start justify-between gap-3">
                <div class="flex items-start space-x-3 min-w-0">
                    <div class="w-12 h-16 bg-[#0C0F17] border border-[#263245] flex-shrink-0 overflow-hidden cursor-pointer" onclick="openCardImageModal('${escapeTwHtml(imgUri)}', '${escapeTwHtml(item.card_in)}', 'ANY', '', 'nonfoil')">
                        ${imgUri ? `<img src="${escapeTwHtml(typeof toScryfallThumb === 'function' ? toScryfallThumb(imgUri) : imgUri)}" alt="${escapeTwHtml(item.card_in)}" loading="lazy" decoding="async" class="w-full h-full object-cover">` : `<div class="p-1 text-[9px] font-mono text-[#64748B] text-center">No Art</div>`}
                    </div>
                    <div class="min-w-0">
                        <div class="flex items-center space-x-2 flex-wrap gap-1">
                            <span class="text-sm font-bold font-heading text-white truncate">${escapeTwHtml(item.card_in)}</span>
                            ${item.is_game_changer ? `<span class="text-[9px] font-mono px-1.5 py-0.2 bg-[#A855F7]/20 text-[#C084FC] border border-[#A855F7]/40 font-bold uppercase">GAME CHANGER</span>` : ''}
                        </div>
                        <div class="text-[11px] font-mono text-[#94A3B8] truncate mt-0.5">
                            ${escapeTwHtml(item.type_line || '')} &middot; CMC ${item.cmc}
                        </div>
                        <div class="flex items-center space-x-2 mt-1 flex-wrap gap-1">
                            ${binderBadge}
                            ${allocBadge}
                        </div>
                    </div>
                </div>
                <div class="text-right flex-shrink-0 font-mono">
                    <div class="text-base font-black ${item.inclusion_pct >= 50 ? 'text-[#10B981]' : (item.inclusion_pct >= 25 ? 'text-[#00CED1]' : 'text-white')}">
                        ${item.inclusion_pct > 0 ? Math.round(item.inclusion_pct) + '%' : 'COMBO'}
                    </div>
                    <div class="text-[10px] text-[#64748B] uppercase">Inclusion</div>
                    ${item.price_usd !== null && item.price_usd !== undefined ? `
                        <div class="text-xs font-bold text-[#C084FC] mt-0.5">$${Number(item.price_usd).toFixed(2)}</div>
                    ` : ''}
                </div>
            </div>

            <!-- Signal Reasons -->
            <div class="flex flex-wrap gap-1.5">
                ${reasonChipsHtml}
            </div>

            ${bracketWarnHtml}
            ${cutHtml}

            <!-- Dismiss Button -->
            <div class="flex items-center justify-end pt-1">
                <button type="button" onclick="dismissTimewalkedSuggestion('${cmdrKey}', '${escapeTwHtml(item.card_in)}')" class="text-[10px] font-mono text-[#64748B] hover:text-[#DC143C] uppercase">
                    ✕ Dismiss from suggestions
                </button>
            </div>
        </div>
    `;
}

function toggleTimewalkedMore(type) {
    if (type === "free") {
        showAllFreeUpgrades = !showAllFreeUpgrades;
        renderUpgradeColumn("free", (currentSuggestedUpgrades && currentSuggestedUpgrades.free_upgrades) || [], showAllFreeUpgrades);
    } else {
        showAllBuyUpgrades = !showAllBuyUpgrades;
        renderUpgradeColumn("buy", (currentSuggestedUpgrades && currentSuggestedUpgrades.buy_upgrades) || [], showAllBuyUpgrades);
    }
}

function dismissTimewalkedSuggestion(cmdrKey, cardName) {
    saveDismissedSuggestion(cmdrKey, cardName);
    if (typeof showToast === "function") {
        showToast(`Dismissed ${cardName}. Click Undo to restore.`, "info", () => {
            removeDismissedSuggestion(cmdrKey, cardName);
            if (lastRequestPayload) loadTimewalkedSuggestedUpgrades(lastRequestPayload);
        });
    }
    // Remove tile immediately
    const cleanId = cardName.replace(/[^a-zA-Z0-9]/g, '');
    const tile = document.getElementById(`tw-tile-${cleanId}`);
    if (tile) tile.remove();
}

async function applyTimewalkedSwap(btn, deckId, cardOut, cardIn) {
    if (!confirm(`Apply swap in your vault deck?\n\nCUT: -1 ${cardOut}\nADD: +1 ${cardIn}`)) return;

    const oldText = btn.textContent;
    btn.textContent = "Swapping...";
    btn.disabled = true;

    try {
        const resp = await fetch(`/api/deck/${deckId}/apply-swap`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ card_out: cardOut, card_in: cardIn })
        });
        const json = await resp.json();
        if (!resp.ok || json.error) {
            throw new Error(json.error || `HTTP ${resp.status}`);
        }
        if (typeof showToast === "function") {
            showToast(`Swapped: -1 ${cardOut} / +1 ${cardIn}`, "success");
        }
        // Re-run analysis for deck to refresh bracket and suggestions
        analyzeVaultDeck(deckId);
    } catch (err) {
        if (typeof showToast === "function") {
            showToast(`Swap failed: ${err.message}`, "error");
        }
        btn.textContent = oldText;
        btn.disabled = false;
    }
}

function copyTimewalkedFreeSwaps() {
    if (!currentSuggestedUpgrades || !currentSuggestedUpgrades.free_upgrades) return;
    const lines = currentSuggestedUpgrades.free_upgrades
        .filter(u => u.card_out)
        .map(u => `+1 ${u.card_in} (from binder: ${u.binders.join(', ') || 'Inventory'}) / -1 ${u.card_out}`);

    if (lines.length === 0) {
        if (typeof showToast === "function") showToast("No free swaps available to copy.", "info");
        return;
    }
    const txt = `// TIMEWALKED FREE BINDER SWAPS FOR ${currentSuggestedUpgrades.commander}\n` + lines.join("\n");
    navigator.clipboard.writeText(txt).then(() => {
        if (typeof showToast === "function") showToast(`Copied ${lines.length} free swaps to clipboard!`, "success");
    });
}

function copyTimewalkedBuyList() {
    if (!currentSuggestedUpgrades || !currentSuggestedUpgrades.buy_upgrades) return;
    const lines = currentSuggestedUpgrades.buy_upgrades.map(u => `1 ${u.card_in}`);
    if (lines.length === 0) {
        if (typeof showToast === "function") showToast("Buy list is empty.", "info");
        return;
    }
    const txt = lines.join("\n");
    navigator.clipboard.writeText(txt).then(() => {
        if (typeof showToast === "function") showToast(`Copied ${lines.length} buy list cards to clipboard!`, "success");
    });
}

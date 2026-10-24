// State Management
const _hubInit = window.__COMMANDER_HUB_INIT__ || {};
const allDecks = Array.isArray(_hubInit.decks) ? _hubInit.decks : [];
const initialFleetStats = _hubInit.fleetStats || {};
let currentVaultDecks = [...allDecks];

function toScryfallThumb(url) {
    if (!url || typeof url !== 'string') return url || '';
    if (url.includes('cards.scryfall.io/normal/')) return url.replace('cards.scryfall.io/normal/', 'cards.scryfall.io/small/');
    if (url.includes('cards.scryfall.io/large/')) return url.replace('cards.scryfall.io/large/', 'cards.scryfall.io/small/');
    return url;
}
let filteredDecks = [...(allDecks && allDecks.length > 0 ? allDecks : currentVaultDecks || [])];
let selectedDeckIds = new Set();
let currentColorFilter = 'all';
let currentFormatFilter = 'all';
let currentSortKey = 'recent';
let currentSortAsc = false;
let currentVaultMode = 'grid';
let currentComparison = null;
let activeDeck = null;
let currentIngestTab = 'url';
let currentCardTypeFilter = 'all';
let currentDeckCards = [];
let currentFilteredCards = [];
let currentCardSortKey = 'rating_desc';
let currentCardRatingFilter = 'all';
let currentCardViewMode = 'auto'; // 'auto', 'tiles', 'table', or 'stacks'
let currentStackGroupBy = 'cmc'; // 'cmc', 'role', or 'type'
let currentCardOwnershipFilter = 'all'; // 'all', 'owned', 'unowned', 'shared', or 'conflict'
let currentCardDrilldownFilter = null; // { type: 'cmc'|'type'|'role', value: string, label: string }
let currentFleetChartMetric = 'power'; // 'power', 'value', or 'synergy'
let fleetPositioningChartInstance = null;

// Universal Card Image Resolver with Scryfall Direct Fallback
function resolveCardImageUri(cardName) {
    if (!cardName) return '';
    const clean = stripDiacritics(cardName).trim().toLowerCase();
    const cleanFront = clean.includes(' // ') ? clean.split(' // ')[0].trim() : clean;

    // 1. Check activeDeck cards_data
    if (activeDeck && Array.isArray(activeDeck.cards_data)) {
        for (const c of activeDeck.cards_data) {
            const cName = stripDiacritics(c.name || '').trim().toLowerCase();
            const cFront = cName.includes(' // ') ? cName.split(' // ')[0].trim() : cName;
            if (cName === clean || cFront === cleanFront) {
                const img = c.image_uri || c.small_image_uri || c.art_crop_uri;
                if (img) return img;
            }
        }
    }

    // 2. Check activeDeck upgrades (owned and market)
    if (activeDeck && activeDeck.upgrades) {
        const up = activeDeck.upgrades;
        const pools = [
            ...(up.owned_upgrades || []),
            ...(up.market_upgrades || []),
            ...(up.budget_swaps || []),
            ...(up.high_impact_swaps || [])
        ];
        for (const u of pools) {
            const inName = stripDiacritics(u.card_in || '').trim().toLowerCase();
            const inFront = inName.includes(' // ') ? inName.split(' // ')[0].trim() : inName;
            if (inName === clean || inFront === cleanFront) {
                if (u.card_in_image) return u.card_in_image;
            }
        }
    }

    // 3. Fallback to Scryfall named image redirect endpoint
    return `https://api.scryfall.com/cards/named?exact=${encodeURIComponent(cleanFront)}&format=image&version=normal`;
}

// Relative Date Formatter
function formatRelativeDate(isoStr) {
    if (!isoStr) return '';
    try {
        const d = new Date(isoStr);
        if (isNaN(d.getTime())) return '';
        const now = new Date();
        const diffMs = now - d;
        if (diffMs < 0) return 'Just now';
        const diffSecs = Math.floor(diffMs / 1000);
        if (diffSecs < 60) return 'Just now';
        const diffMins = Math.floor(diffSecs / 60);
        if (diffMins < 60) return `${diffMins}m ago`;
        const diffHours = Math.floor(diffMins / 60);
        if (diffHours < 24) return `${diffHours}h ago`;
        const diffDays = Math.floor(diffHours / 24);
        if (diffDays === 1) return 'Yesterday';
        if (diffDays < 7) return `${diffDays}d ago`;
        if (diffDays < 30) return `${Math.floor(diffDays / 7)}w ago`;
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    } catch (_) {
        return '';
    }
}

// =========================================================================
// STRATEGIC EDH ANALYTICS TOOLTIP ENCYCLOPEDIA & HUD ENGINE
// =========================================================================
const EDH_ANALYTICS_TOOLTIPS = {
    'arsenal_fleet': {
        category: 'OVERVIEW',
        title: 'Arsenal Size',
        meaning: 'The total number of Commander decks cataloged and tracked within your physical or digital collection.',
        calculation: 'Sum of all unique deck lists saved in the Chimaera SQLite vault database.',
        relevance: 'Provides high-level collection perspective, helping identify gaps in archetype variety, color saturation, and budget allocation across your pod.',
        benchmark: 'Pod dependent (Typical active collection: 3 – 12 decks)'
    },
    'portfolio_value': {
        category: 'FINANCIAL TELEMETRY',
        title: 'Total Portfolio / Deck Market Value',
        meaning: 'The live replacement market value of the cards in your deck or across your entire collection, based on TCGplayer Market pricing.',
        calculation: 'Sum of Scryfall/TCGplayer market USD prices for all 100 cards: Total = Σ(Card_Price × Quantity). Basic lands count as $0.00.',
        relevance: 'Helps evaluate collection equity, assess budget brackets for playgroup rule-zero discussions, and determine price-to-power efficiency.',
        benchmark: '$50 – $150 (Budget), $200 – $500 (Mid-Power), $1,000+ (High/cEDH)'
    },
    'fleet_power': {
        category: 'OVERVIEW',
        title: 'Mean Power Level',
        meaning: 'The aggregate average competitive power rating across all analyzed decks in your collection on a standard 1.0 to 10.0 scale.',
        calculation: 'Collection Average = Σ(Deck Power Level) / Count(Analyzed Decks), weighted by mana curve, tutor density, fast ramp, and AI evaluation.',
        relevance: 'Ensures you bring appropriately calibrated decks to game nights and helps maintain a balanced power dynamic within your playgroup.',
        benchmark: '6.0 – 7.5 (Casual/Focused Mid-Power average)'
    },
    'fleet_speed': {
        category: 'OVERVIEW',
        title: 'Average Pacing (Avg CMC)',
        meaning: 'The mean Average Mana Value (AMV) across all nonland spells in your entire collection of Commander decks.',
        calculation: 'Average AMV = Σ(All Nonland CMC) / Σ(All Nonland Spells across all decks).',
        relevance: 'Indicates whether your collection leans toward fast, low-to-the-ground burst decks or heavy, battlecruiser late-game strategies.',
        benchmark: '3.00 – 3.35 CMC (Collection average)'
    },
    'color_wheel': {
        category: 'OVERVIEW',
        title: 'Color Distribution & Saturation',
        meaning: 'The frequency and representation of the 5 Magic colors (White, Blue, Black, Red, Green) and Colorless across your entire collection.',
        calculation: 'Color Share % = (Decks containing Color C / Total Decks) × 100.',
        relevance: 'Reveals personal deckbuilding color biases, overlaps in staple mana bases (fetchlands/shocklands), and unrepresented color pairs in your roster.',
        benchmark: 'Even 20% distribution per color represents complete color balance'
    },
    'col_cmdr': {
        category: 'MATRIX TELEMETRY',
        title: 'Commander & Identity',
        meaning: 'The legendary creature(s) or planeswalker leading the deck and determining its color identity rules.',
        calculation: 'Extracted from Scryfall color_identity and card types. Color identity includes casting cost pips and rules text mana symbols.',
        relevance: 'The commander defines your deck’s strategy, access to colors, and repeatable command zone card advantage.',
        benchmark: '100-card singleton legality rule compliance'
    },
    'col_colors': {
        category: 'MATRIX TELEMETRY',
        title: 'Color Identity',
        meaning: 'The color identity combination of the deck (Mono, Guild, Shard/Wedge, 4-Color, or 5-Color).',
        calculation: 'Union of all mana symbols in the commander’s mana cost and rules text.',
        relevance: 'Determines color fixing requirements, susceptibility to nonbasic land hate (Blood Moon), and access to color-specific interaction and tutor suites.',
        benchmark: '1–3 Colors recommended for optimal mana consistency'
    },
    'col_power': {
        category: 'TACTICAL TELEMETRY',
        title: 'Commander Power Level',
        meaning: 'Normalized 1.0–10.0 rating benchmarking deck velocity, interaction speed, synergy, and combo resilience against the global EDH metagame.',
        calculation: 'Algorithmic synthesis of fast ramp count, removal efficiency, tutor density, average CMC, and Gemini AI strategic assessment.',
        relevance: 'Critical for Rule 0 pre-game discussions to avoid pubstomping and ensure balanced 4-player games.',
        benchmark: '5-6: Casual Precon+, 7-8: Focused/Optimized, 9-10: Max Power/cEDH'
    },
    'col_value': {
        category: 'FINANCIAL TELEMETRY',
        title: 'TCG Market Value',
        meaning: 'Real-time market valuation of all 100 cards aggregated from TCGplayer Market prices via Scryfall.',
        calculation: 'Σ(Card Price USD × Quantity). Unpriced tokens/basics are counted as $0.00.',
        relevance: 'Allows evaluating whether deck performance is driven by raw expensive staples (Mana Crypt, Duals) or tight mechanical synergy.',
        benchmark: '$75 – $300 (Standard Casual/Focused)'
    },
    'col_cmc': {
        category: 'SPEED & MANA',
        title: 'Average Nonland CMC',
        meaning: 'The mean converted mana cost of every nonland card in the 99-card deck and command zone.',
        calculation: 'AMV = Σ(Card CMC for non-lands) / Total Nonland Cards. X-spells count X as 0.',
        relevance: 'Lower AMV allows you to cast multiple spells per turn and hold up interaction on opponents’ turns.',
        benchmark: '2.80 – 3.30 (Casual/Mid), < 2.40 (cEDH)'
    },
    'col_speed': {
        category: 'SPEED & MANA',
        title: 'Speed & Pacing Telemetry',
        meaning: 'Composite metrics measuring early-game consistency: Turn 3 Land Drop %, Opening Hand Keepability %, and Median Commander Cast Turn.',
        calculation: 'Derived from Hypergeometric distribution of lands and 5,000-run Monte Carlo opening hand simulations.',
        relevance: 'Determines if your deck stumbles in early turns (turns 1-3) or consistently curves out ahead of opponents.',
        benchmark: 'T3 Land > 80%, Keepability > 80%, Cast Turn ≤ T4'
    },
    'col_winpath': {
        category: 'ENDGAME & WINCONS',
        title: 'Win Path & Late-Game Outlets',
        meaning: 'The primary lethal vector (Combat, Combo, Aristocrats, Incidental Burn) and the count of repeatable late-game mana sinks.',
        calculation: 'Pattern-matched card signatures (infinite combos, overrun effects, storm engines) + repeatable mana sink activation abilities.',
        relevance: 'Ensures the deck can close out 120 life in 4-player pods without stalling or running out of steam when flooded.',
        benchmark: '1 Primary Wincon + 1 Secondary Backup + ≥ 3 Sinks'
    },
    'col_composition': {
        category: 'DECK COMPOSITION',
        title: 'Land vs. Spell Ratio',
        meaning: 'The structural balance between mana-producing lands and proactive/reactive nonland spells.',
        calculation: 'Lands Count vs Nonland Spells Count (e.g. 36 Lands / 64 Spells = 36% / 64%).',
        relevance: 'The foundation of draw consistency. Too few lands leads to mana screw; too many causes late-game topdeck exhaustion.',
        benchmark: '35 – 38 Lands (Normal curve), 30 – 34 Lands (Heavy low-CMC ramp/cEDH)'
    },
    'col_ai': {
        category: 'AI DIAGNOSTICS',
        title: 'Gemini AI Tactical Intel',
        meaning: 'Status of Google Gemini multi-turn strategic assessment and card-by-card 1–10 effectiveness scoring.',
        calculation: 'Processed via Gemini Flash models examining commander synergies, combo lines, and cut/upgrade candidates.',
        relevance: 'Provides personalized strategic advice, identifying non-obvious dead cards and optimal upgrade swaps from your ManaBox binder.',
        benchmark: "'Analyzed' status indicates full strategic telemetry active"
    },
    'power_level': {
        category: 'TACTICAL TELEMETRY',
        title: 'Tactical Power Level (1.0 – 10.0)',
        meaning: 'A standardized composite rating measuring deck velocity, interaction density, tutor count, and win condition speed in Commander.',
        calculation: 'Algorithmic composite: Base Power = f(AMV, Fast Ramp, Removal Efficiency, Tutors, Wincon Turn) calibrated with Gemini strategic audit.',
        relevance: 'Sets expectations for Rule 0 discussions before starting a game. Prevents accidental pod mismatches and salt.',
        benchmark: '1-4: Casual/Jank, 5-6: Precon+, 7-8: Optimized Mid-Power, 9-10: High/cEDH'
    },
    'power_bracket': {
        category: 'TACTICAL TELEMETRY',
        title: 'Power Bracket Classification',
        meaning: 'Metagame bracket category grouping decks by intended play environment and table speed.',
        calculation: 'Tier mapped directly from Power Level: Casual (< 6.0), Focused (6.0 - 7.4), Optimized (7.5 - 8.9), Competitive cEDH (≥ 9.0).',
        relevance: 'Helps you categorize decks quickly when organizing game nights or selecting pods with friends.',
        benchmark: 'Match deck bracket with all 3 pod opponents for optimal gameplay'
    },
    'archetype': {
        category: 'TACTICAL TELEMETRY',
        title: 'Strategic Deck Archetype',
        meaning: 'The macro-strategy and operational framework of your deck (e.g., Aristocrats, Spellslinger, Voltron, Stax, Ramp, Combo, Tokens, Control).',
        calculation: 'Identified via card keyword density, commander static/triggered abilities, and wincon card clustering.',
        relevance: 'Dictates optimal mulligan decisions, target prioritization, and game pacing against opposing deck archetypes.',
        benchmark: 'Aligned with commander’s core mechanical text'
    },
    'edhrec_stats': {
        category: 'METAGAME TELEMETRY',
        title: 'EDHREC Rank, Deck Count & Salt Score',
        meaning: 'Global community popularity ranking, total number of registered decks on EDHREC, and community-voted Salt Score (0.00 - 4.00).',
        calculation: 'Direct live sync from EDHREC API for the active commander. Salt score aggregates community votes on unfun/oppressive cards.',
        relevance: 'High salt scores indicate cards or commanders that generate table grievance (e.g., Vorinclex, Armageddon, Tergrid); high ranks mean opponents know your lines.',
        benchmark: 'Rank < 200 (Highly Popular), Salt < 0.75 (Low Salt / Casual Friendly)'
    },
    'amv': {
        category: 'SPEED & MANA',
        title: 'Nonland Average Mana Value (AMV)',
        meaning: 'The mathematical average converted mana cost of all nonland cards in the deck.',
        calculation: 'AMV = Σ(Converted Mana Cost of all nonland cards) / Total Nonland Cards. Lands are excluded to measure true spell mana weight.',
        relevance: 'In Commander, games are often won by mana efficiency—casting multiple spells per turn cycle while holding up mana for reactive interaction.',
        benchmark: '2.80 – 3.30 (Casual/Mid), 2.00 – 2.50 (High/cEDH)'
    },
    'fast_ramp': {
        category: 'SPEED & MANA',
        title: 'Fast Mana & Early Ramp (CMC ≤ 2)',
        meaning: 'Permanent mana accelerators that cost 2 or fewer mana (e.g., Sol Ring, Arcane Signet, Llanowar Elves, Nature’s Lore, Talismans).',
        calculation: 'Count of all artifacts, creatures, and sorceries that cost ≤ 2 mana and produce or search for permanent mana.',
        relevance: 'Turn 1-2 ramp accelerates your curve by a full turn cycle, allowing you to cast 4-drops on Turn 3 and establish engine dominance before opponents.',
        benchmark: '8 – 12 Fast Ramp pieces (Normal), 14+ (Turbo/High Power)'
    },
    'instant_ratio': {
        category: 'INTERACTION & DEFENSE',
        title: 'Instant-Speed Interaction %',
        meaning: 'The percentage of your nonland interaction, removal, and utility cards that can be cast at instant speed on opponents’ turns.',
        calculation: 'Instant % = (Instant Spells + Flash Permanents) / Total Nonland Spells × 100.',
        relevance: 'Sorcery speed removal lets three opponents untap and exploit the board first. Instant-speed spells allow you to respond to game-winning combos and board wipes.',
        benchmark: '≥ 50% of interaction suite should be Instant speed'
    },
    'tapland_ratio': {
        category: 'SPEED & MANA',
        title: 'Tapland Tempo Penalty Index',
        meaning: 'The percentage of lands in your mana base that enter the battlefield tapped unconditionally or with difficult conditions.',
        calculation: 'Tapland % = (Lands Entering Tapped / Total Lands) × 100. Measures virtual mana wasted on ETB turn.',
        relevance: 'Every tapland played sets you exactly one turn behind on curve tempo. Multiple taplands cause you to cast spells a turn later than opponents on untapped lands.',
        benchmark: '< 15% (Healthy Mid-Power), < 5% (Optimized), 0% (cEDH)'
    },
    'draw_engines': {
        category: 'CARD ADVANTAGE & SYNERGY',
        title: 'Repeatable Card Draw Engines',
        meaning: 'Permanents that draw cards repeatedly over multiple turns or triggers (e.g., Rhystic Study, Beast Whisperer, Esper Sentinel, Skullclamp, Phyrexian Arena).',
        calculation: 'Count of nonland permanents with repeatable card draw static abilities or activated/triggered draw mechanisms.',
        relevance: 'In a 4-player game with 3 opponents drawing 3 cards per turn cycle, one-shot draw spells run dry. Repeatable engines provide sustained gas to stay ahead.',
        benchmark: '5 – 8 Repeatable Draw Engines'
    },
    'tcg_value': {
        category: 'FINANCIAL TELEMETRY',
        title: 'TCGplayer Market Deck Value',
        meaning: 'Current fair market replacement cost of the physical cards in the deck, updated live via Scryfall market pricing.',
        calculation: 'Sum of individual card prices: Total USD = Σ(Market Price × Quantity). Basic lands count as $0.00.',
        relevance: 'Enables budget monitoring, insurance tracking, and analyzing whether deck performance stems from expensive reserve list staples or smart synergy.',
        benchmark: '$75 – $250 (Budget/Focused), $300 – $800 (Optimized)'
    },
    'mana_curve': {
        category: 'SPEED & MANA',
        title: 'Nonland Mana Value Curve',
        meaning: 'The distribution histogram of nonland cards across mana values from 0 CMC to 7+ CMC.',
        calculation: 'Binned counts of nonland spells by CMC: [0, 1, 2, 3, 4, 5, 6, 7+].',
        relevance: 'A healthy curve resembles a bell curve centered around 2-3 CMC with a steep drop-off at 5+. Top-heavy curves cause uncastable opening hands and tempo loss.',
        benchmark: 'Peak at 2 and 3 CMC; ≤ 6 cards at 6+ CMC unless specialized ramp'
    },
    'card_types': {
        category: 'DECK COMPOSITION',
        title: 'Card Type Breakdown',
        meaning: 'The proportional allocation of cards across Magic’s major types: Creatures, Instants, Sorceries, Artifacts, Enchantments, Planeswalkers, and Lands.',
        calculation: 'Percentage and count per primary card type out of the 100-card deck.',
        relevance: 'Determines vulnerability to mass removal (Farewell, Bane of Progress) and synergy with type-specific card draw (Beast Whisperer vs Archmage Emeritus).',
        benchmark: '35-38 Lands, 25-35 Creatures (in creature decks), 10-18 Instants'
    },
    'pip_source_balance': {
        category: 'SPEED & MANA',
        title: 'Mana Pip Demand vs. Source Supply Balance',
        meaning: 'Radar comparison matching colored mana pip symbols on your spells against colored mana production sources from lands and mana rocks.',
        calculation: 'Pip Demand % = (Color Pips / Total Colored Pips) × 100. Source Supply % = (Color Sources / Total Colored Sources) × 100.',
        relevance: 'Color imbalance causes "color screw"—having mana available but lacking the specific colored pips (e.g. UU for Counterspell) to cast your cards on time.',
        benchmark: 'Delta between Demand % and Supply % within ± 5% for each color'
    },
    'speed_pacing': {
        category: 'SPEED & MANA',
        title: 'Velocity & Mana Base Telemetry',
        meaning: 'Holistic evaluation of deck velocity, including fast mana rocks, dorks, land-fetch spells, and treasure creation engines.',
        calculation: 'Aggregated count of mana ramp (CMC ≤ 2 vs CMC ≥ 3) + tapland penalty percentage.',
        relevance: 'High velocity decks develop mana faster than opponents, deploying threats and engines 1 to 2 turns ahead of the table curve.',
        benchmark: '10 – 14 Total Acceleration Sources'
    },
    'interaction_defense': {
        category: 'INTERACTION & DEFENSE',
        title: 'Interaction & Removal Suite',
        meaning: 'The complete tactical package of targeted spot removal, board wipes, counterspells, and permanent protection.',
        calculation: 'Σ(Targeted Spot Removal) + Σ(Mass Board Wipes) + Σ(Counterspells) + Σ(Protection/Phase Out).',
        relevance: 'Without sufficient interaction, your opponents will assemble game-winning loops or runaway engines uninterrupted.',
        benchmark: '10 – 14 Targeted Removal, 2 – 4 Board Wipes, 4 – 8 Protection/Counters'
    },
    'card_advantage': {
        category: 'CARD ADVANTAGE & SYNERGY',
        title: 'Card Advantage & Tutor Velocity',
        meaning: 'The combined card flow suite consisting of repeatable draw engines, burst draw spells, cheap cantrips, and targeted tutors.',
        calculation: 'Engines + Burst Draw + Cantrips + General Tutors + Land Fetch Tutors.',
        relevance: 'In multiplayer Magic, card advantage is the #1 predictor of victory. Refills your hand after board wipes and finds win conditions.',
        benchmark: '10 – 14 Total Card Advantage sources, 2 – 4 Tutors'
    },
    'land_drops': {
        category: 'SPEED & MANA',
        title: 'Hypergeometric Land Drop Consistency',
        meaning: 'The exact mathematical probability of drawing and playing lands on curve for Turns 1, 2, 3, 4, and 5 without missing a drop.',
        calculation: 'Calculated via Cumulative Hypergeometric Distribution: P(X ≥ k) = Σ( [C(M, i) × C(N-M, n-i)] / C(N, n) ) where N=99, M=Lands, n=7+Turn-1 cards drawn.',
        relevance: 'Missing your 3rd or 4th land drop drops your win rate dramatically. Turn 3 land drop consistency should always exceed 80%.',
        benchmark: 'Turn 3 ≥ 82%, Turn 4 ≥ 68%, Turn 5 ≥ 50%'
    },
    'opening_hand': {
        category: 'SPEED & MANA',
        title: 'Opening Hand Keepability Rate',
        meaning: 'The simulated percentage of opening 7-card hands that are viable keeps under the London Mulligan rule.',
        calculation: '5,000-iteration Monte Carlo simulation. Hands with 2-5 lands, ≥ 1 colored source matching early spells, and ≥ 1 spell with CMC ≤ 3 are scored as Keepable.',
        relevance: 'High unkeepable rates force aggressive mulligans to 6 or 5 cards, starting the game down card advantage before Turn 1 begins.',
        benchmark: '≥ 80% Keepable Hands, < 10% Unkeepable Hands'
    },
    'commander_cast': {
        category: 'SPEED & MANA',
        title: 'Earliest & Median Commander Cast Turn',
        meaning: 'The simulated game turn on which you are statistically able to cast your Commander for the first time, accounting for lands, ramp, and color fixing.',
        calculation: '2,000-game Monte Carlo simulation tracking mana generation curve and colored pip availability turn-by-turn.',
        relevance: 'Tells you when your deck’s core engine comes online and highlights color bottlenecks that delay your commander.',
        benchmark: 'Cast turn should match or beat Commander CMC (e.g., CMC 4 cast on Turn 3 or 4)'
    },
    'threat_coverage': {
        category: 'INTERACTION & DEFENSE',
        title: 'Threat Coverage Matrix (7 Categories)',
        meaning: 'Evaluation of your interaction package across all 7 card threat types: Creatures, Artifacts, Enchantments, Planeswalkers, Graveyards, Lands, and Spells (Stack).',
        calculation: 'Parsing of removal spell targeting text, categorized into Instant vs Sorcery speed answers per threat type.',
        relevance: 'Blind spots (e.g. zero artifact removal vs Torpor Orb, or zero graveyard hate vs Reanimator) can leave you completely helpless against specific pod archetypes.',
        benchmark: 'Answers for at least 5 of 7 categories, including graveyard hate and stack answers'
    },
    'counter_vs_protection': {
        category: 'INTERACTION & DEFENSE',
        title: 'Stack Control vs. Board Protection',
        meaning: 'The ratio of proactive stack interruption (Counterspells) to reactive defensive protection (Hexproof, Indestructible, Phasing, Ward, Flicker).',
        calculation: 'Counterspell Count vs Protection Spell Count. Stance classified as Stack-Heavy, Protective, or Balanced.',
        relevance: 'Counterspells stop combos and board wipes before they resolve; protection spells preserve existing board investments through targeted removal and wipes.',
        benchmark: '4 – 7 Counterspells in Blue; 3 – 5 Protection spells in Green/White'
    },
    'instant_holdout': {
        category: 'INTERACTION & DEFENSE',
        title: 'Instant-Speed Mana Holdout Cost',
        meaning: 'The average mana you must keep unspent when passing your turn in order to hold up your available instant-speed interaction and protection.',
        calculation: 'Holdout AMV = Σ(CMC of all Instant-speed removal & counterspells) / Total Instant Interaction Spells.',
        relevance: 'If your holdout cost is 3-4 CMC, you are forced to choose between progressing your board and keeping up answers. Lower holdout (1-2 CMC) allows doing both.',
        benchmark: '≤ 2.0 CMC (Efficient/Optimized), > 2.8 CMC (Heavy/Clunky)'
    },
    'enabler_payoff': {
        category: 'CARD ADVANTAGE & SYNERGY',
        title: 'Engine Enabler-to-Payoff Ratio',
        meaning: 'The mathematical ratio between setup cards ("Enablers" like sacrifice fodder, discard outlets, self-mill) and reward cards ("Payoffs" like Blood Artist, Syr Konrad).',
        calculation: 'Ratio = Total Enablers / Total Payoffs. Target ratio is approximately 2.0 to 2.5 enablers for every 1 payoff.',
        relevance: 'Too many payoffs without enablers leaves you with dead high-cost cards; too many enablers without payoffs means you build engines that do not win.',
        benchmark: '1.8 : 1 to 2.5 : 1 (Healthy Engine Balance)'
    },
    'typal_synergy': {
        category: 'CARD ADVANTAGE & SYNERGY',
        title: 'Typal / Kindred Density & Support',
        meaning: 'For creature-type focused decks (Elves, Dragons, Vampires, Zombies), measures the density of the chosen type and dedicated kindred lord/support cards.',
        calculation: 'Matching Creature Count / Total Creatures × 100 + Count of dedicated typal support spells (lords, anthems, kindred tutors).',
        relevance: 'Typal payoff cards (e.g. Elvish Archdruid, Crux of Fate) become dead draws if your creature type density drops below critical threshold.',
        benchmark: '≥ 22-26 Matching Creatures (65%+ of creatures) for dedicated typal decks'
    },
    'virtual_ca': {
        category: 'CARD ADVANTAGE & SYNERGY',
        title: 'Virtual Card Advantage Index',
        meaning: 'Comprehensive card velocity combining raw card draw, impulse exile draw, graveyard recursion, and targeted tutors into a single card-flow score.',
        calculation: 'VCA Score = Raw Draw Spells + (0.9 × Impulse Draw) + (0.85 × Recursion Spells) + (1.2 × Tutors).',
        relevance: 'Modern Commander utilizes red impulse draw and black/green graveyard loops just as effectively as blue card draw. Measures total card access.',
        benchmark: '≥ 12 – 16 Total Virtual Card Advantage sources'
    },
    'win_conditions': {
        category: 'ENDGAME & WINCONS',
        title: 'Primary & Secondary Win Paths',
        meaning: 'Algorithmic classification of the primary lethal vector (Combat Swarm, Infinite Combo, Commander Damage/Voltron, Aristocrats Drain, Incidental Burn).',
        calculation: 'Identifies game-winning loops from Commander Spellbook, high-power overrun spells (Craterhoof, Triumph of the Hordes), and repeatable drain triggers.',
        relevance: 'Decks with no clear win path stall games into 2-hour board stalls. Having distinct primary and backup wincons ensures you can finish the game.',
        benchmark: '1 Dedicated Primary Win Path + 1 Resilient Backup Path'
    },
    'mana_sinks': {
        category: 'ENDGAME & WINCONS',
        title: 'Late-Game Mana Sinks & Outlets',
        meaning: 'Repeatable activated abilities, utility lands, and X-cost spells that can absorb excess mana on turns 7+ to prevent flooding.',
        calculation: 'Count of cards featuring repeatable mana activation costs (e.g., "{4}: Draw a card", "{X}: Create tokens") or "{X}" casting costs.',
        relevance: 'Commander games often stall into late-game mana flood. Mana sinks turn 10+ spare mana into game-winning card advantage or lethal board pressure.',
        benchmark: '≥ 3 – 5 Repeatable Mana Outlets or X-spells'
    },
    'card_ratings': {
        category: 'AI DIAGNOSTICS',
        title: 'Card Effectiveness Rating (1.0 – 10.0)',
        meaning: 'Individual card performance score evaluating mana efficiency, synergy with commander, and floor/ceiling performance in 4-player games.',
        calculation: 'Evaluated by Gemini AI contextual model: 9-10 (Core Staples), 7-8.9 (Strong Synergizers), 5-6.9 (Solid Role Players), < 5.0 (Cut Candidates).',
        relevance: 'Quickly pinpoints low-impact or outdated cards in your 99 to replace with high-synergy upgrades from your physical collection.',
        benchmark: 'Average deck rating ≥ 7.2; cuts recommended for cards rated < 5.0'
    },
    'color_distribution': {
        category: 'OVERVIEW',
        title: 'Color Frequency & Representation',
        meaning: 'Distribution of colors across all decks in your vault, showing which color slices you play most and least.',
        calculation: 'Count and percentage of decks containing White, Blue, Black, Red, Green, or Colorless.',
        relevance: 'Helps you balance your deck portfolio and prevents building repetitive decks that compete for the exact same staple mana base cards.',
        benchmark: 'Pod dependent; ideal collection has at least one deck in each color combination'
    },
    'speed_vs_budget': {
        category: 'OVERVIEW',
        title: 'Speed vs. Budget Positioning',
        meaning: 'Matrix positioning of each deck comparing its speed (Avg CMC) against its financial investment (TCGplayer Market Value).',
        calculation: 'Plotted as coordinate (Avg CMC, TCG Market Value) across all decks in your Vault.',
        relevance: 'Identifies ultra-efficient budget gems (low CMC, low price) versus inefficient "dollar-heavy" decks that run expensive, slow cards.',
        benchmark: 'High power efficiency = Low CMC (< 3.0) with balanced budget'
    },
    'ramp_density': {
        category: 'SPEED & MANA',
        title: 'Mana Dorks, Rocks & Ramp Spells',
        meaning: 'Total permanent and spell acceleration including mana dorks (creatures), mana rocks (artifacts), and land-search sorceries.',
        calculation: 'Total Ramp = Dorks + Rocks + Land Ramp (Rampant Growth, Cultivate) + Rituals.',
        relevance: 'Guarantees you reach your commander and high-impact spells 1 to 2 turns ahead of the natural curve.',
        benchmark: '10 – 14 Ramp sources'
    },
    'treasure_count': {
        category: 'SPEED & MANA',
        title: 'Treasure & Burst Mana Generation',
        meaning: 'Cards that create artifact Treasure tokens for flexible, multi-color burst mana acceleration.',
        calculation: 'Count of all nonland cards with "create... Treasure token" text in rules.',
        relevance: 'Treasures provide temporary color fixing, storm mana for explosive multi-spell turns, and artifact sacrifice synergies.',
        benchmark: 'Archetype dependent (3 – 8 in Black/Red/Green treasure decks)'
    },
    'targeted_removal': {
        category: 'INTERACTION & DEFENSE',
        title: 'Targeted Spot Removal Spells',
        meaning: 'Single-target removal spells that destroy, exile, or bounce problematic opposing permanents.',
        calculation: 'Count of nonland spells with "destroy target", "exile target", or "return target permanent to its owner\'s hand".',
        relevance: 'Essential for removing high-threat commanders, combo pieces, or stax pieces without resetting the entire board state.',
        benchmark: '8 – 12 Targeted Removal spells'
    },
    'board_wipes': {
        category: 'INTERACTION & DEFENSE',
        title: 'Mass Board Wipes & Sweepers',
        meaning: 'Symmetrical mass removal spells that destroy, exile, or bounce all or most creatures, artifacts, or permanents.',
        calculation: 'Count of spells with "destroy all", "exile all", or mass damage effects (e.g. Blasphemous Act, Toxic Deluge, Wrath of God).',
        relevance: 'The ultimate reset button when falling behind in 4-player games against go-wide token swarms or overwhelming boards.',
        benchmark: '2 – 4 Mass Board Wipes'
    },
    'removal_efficiency': {
        category: 'INTERACTION & DEFENSE',
        title: 'Removal Mana Efficiency',
        meaning: 'The average converted mana cost of your targeted removal and interaction spells.',
        calculation: 'Efficiency CMC = Σ(CMC of targeted removal spells) / Count of targeted removal spells.',
        relevance: 'High-CMC removal (4-5 CMC) leaves you tapped out and vulnerable. Low-CMC removal (1-2 CMC) allows answering threats while still developing your board.',
        benchmark: '≤ 2.20 CMC (High Efficiency), 2.20 – 3.00 CMC (Normal)'
    },
    'burst_draw': {
        category: 'CARD ADVANTAGE & SYNERGY',
        title: 'Burst Card Draw Spells',
        meaning: 'One-shot spells that draw multiple cards in a single resolution (e.g., Harmonize, Fact or Fiction, Rishkar’s Expertise, Stroke of Genius).',
        calculation: 'Count of non-permanent instants, sorceries, or ETBs that draw ≥ 2 cards upon resolution.',
        relevance: 'Provides an immediate hand refill after spending your resources or recovering from a board wipe.',
        benchmark: '3 – 6 Burst Draw spells'
    },
    'cantrips': {
        category: 'CARD ADVANTAGE & SYNERGY',
        title: 'Low-CMC Cantrips',
        meaning: '1-to-2 CMC spells that replace themselves immediately by drawing a card while providing minor utility or selection (e.g., Ponder, Brainstorm, Consider).',
        calculation: 'Count of nonland cards costing ≤ 2 mana with "draw a card" in rules.',
        relevance: 'Smooths early-game land drops, digs for key answers, and triggers spellslinger and magecraft mechanics cheaply.',
        benchmark: '0 – 4 in Midrange; 8 – 14 in Spellslinger/Combo'
    },
    'tutors': {
        category: 'CARD ADVANTAGE & SYNERGY',
        title: 'Tutor Density (General & Land)',
        meaning: 'Cards that search your library for a specific card and put it into your hand, graveyard, or onto the battlefield.',
        calculation: 'General Tutors (search for any card/permanent) + Land Tutors (search for specific lands).',
        relevance: 'Tutors dramatically increase deck consistency, effectively functioning as extra copies of your best win conditions and silver-bullet answers.',
        benchmark: '0 – 1 (Casual), 2 – 4 (Focused/Optimized), 5+ (High Power/cEDH)'
    },
    'deck_composition': {
        category: 'DECK COMPOSITION',
        title: 'Deck Composition & Land Ratio',
        meaning: 'The overall macro-split between lands (mana resource) and nonland spells (action cards).',
        calculation: 'Percentage and count of Lands vs Spells (Total cards = 100).',
        relevance: 'Determines overall draw reliability and resistance to mana flood versus mana screw across multi-turn games.',
        benchmark: '35 – 38% Lands (35-38 lands), 62 – 65% Nonland Spells'
    }
};

let currentActiveTooltipKey = null;

function showAnalyticsTooltip(key, event) {
    if (!key) return;
    const data = EDH_ANALYTICS_TOOLTIPS[key];
    if (!data) return;

    currentActiveTooltipKey = key;
    const popover = document.getElementById('analyticsTooltipPopover');
    if (!popover) return;

    safeSetText('ttCategory', data.category || 'TACTICAL TELEMETRY');
    safeSetText('ttTitle', data.title || key);
    safeSetText('ttMeaning', data.meaning || '');
    safeSetText('ttCalculation', data.calculation || '');
    safeSetText('ttRelevance', data.relevance || '');
    safeSetText('ttBenchmark', data.benchmark || 'N/A');

    popover.classList.remove('hidden');
    positionAnalyticsTooltip(event);
}

function moveAnalyticsTooltip(event) {
    if (!currentActiveTooltipKey) return;
    positionAnalyticsTooltip(event);
}

function hideAnalyticsTooltip(force = false) {
    const popover = document.getElementById('analyticsTooltipPopover');
    if (!popover) return;
    popover.classList.add('hidden');
    currentActiveTooltipKey = null;
}

function positionAnalyticsTooltip(e) {
    const popover = document.getElementById('analyticsTooltipPopover');
    if (!popover || popover.classList.contains('hidden')) return;

    if (window.innerWidth < 640) {
        popover.style.left = '1rem';
        popover.style.right = '1rem';
        popover.style.bottom = '1rem';
        popover.style.top = 'auto';
        popover.style.maxWidth = 'calc(100vw - 2rem)';
        return;
    }

    const margin = 16;
    const popoverRect = popover.getBoundingClientRect();
    const w = popoverRect.width || 380;
    const h = popoverRect.height || 260;

    let clientX = e ? (e.clientX || (e.touches && e.touches[0] ? e.touches[0].clientX : 0)) : 0;
    let clientY = e ? (e.clientY || (e.touches && e.touches[0] ? e.touches[0].clientY : 0)) : 0;

    let x = clientX + 16;
    let y = clientY + 16;

    if (x + w > window.innerWidth - margin) {
        x = clientX - w - 16;
    }
    if (y + h > window.innerHeight - margin) {
        y = clientY - h - 16;
    }

    x = Math.max(margin, Math.min(x, window.innerWidth - w - margin));
    y = Math.max(margin, Math.min(y, window.innerHeight - h - margin));

    popover.style.left = `${x}px`;
    popover.style.top = `${y}px`;
    popover.style.right = 'auto';
    popover.style.bottom = 'auto';
}

function setupAnalyticsTooltips() {
    // Delegated listeners for hover and click interactions
    document.addEventListener('mouseover', (e) => {
        const target = e.target.closest('[data-tooltip-key]');
        if (target) {
            const key = target.getAttribute('data-tooltip-key');
            if (key) showAnalyticsTooltip(key, e);
        }
    });

    document.addEventListener('mousemove', (e) => {
        if (currentActiveTooltipKey) {
            moveAnalyticsTooltip(e);
        }
    });

    document.addEventListener('mouseout', (e) => {
        const target = e.target.closest('[data-tooltip-key]');
        if (target) {
            const related = e.relatedTarget ? e.relatedTarget.closest('[data-tooltip-key]') : null;
            if (related !== target) {
                hideAnalyticsTooltip();
            }
        }
    });

    // Touch & mobile click support
    document.addEventListener('click', (e) => {
        const target = e.target.closest('[data-tooltip-key]');
        const popover = document.getElementById('analyticsTooltipPopover');
        if (target) {
            const key = target.getAttribute('data-tooltip-key');
            if (key) {
                if (currentActiveTooltipKey === key && popover && !popover.classList.contains('hidden')) {
                    hideAnalyticsTooltip();
                } else {
                    showAnalyticsTooltip(key, e);
                }
            }
        } else if (popover && !popover.contains(e.target)) {
            hideAnalyticsTooltip();
        }
    });
}

document.addEventListener('DOMContentLoaded', async () => {
    setupAnalyticsTooltips();
    renderVaultGrid(currentVaultDecks);
    renderMatrixTable(filteredDecks);
    updateDeckDropdowns(currentVaultDecks);

    // Auto-select first 2 decks for comparison dropdowns if available
    const decksForCompare = (allDecks && allDecks.length > 0) ? allDecks : currentVaultDecks;
    if (decksForCompare && decksForCompare.length >= 1) {
        const s1 = document.getElementById('compareSelect1');
        if (s1) s1.value = decksForCompare[0].id;
        selectedDeckIds.add(decksForCompare[0].id);
    }
    if (decksForCompare && decksForCompare.length >= 2) {
        const s2 = document.getElementById('compareSelect2');
        if (s2) s2.value = decksForCompare[1].id;
        selectedDeckIds.add(decksForCompare[1].id);
    }
    updateSelectedCountUI();

    const urlParams = new URLSearchParams(window.location.search);
    const targetDeckId = urlParams.get('deck_id') || urlParams.get('deck') || (_hubInit.initialDeckId || '');
    const initView = urlParams.get('view') || (_hubInit.initialView || '');
    const initTab = urlParams.get('tab') || (_hubInit.initialTab || '');

    if (targetDeckId) {
        await loadSavedDeckIntoInspector(targetDeckId, initTab);
    } else if (initView === 'matrix') {
        switchMainView('vault');
        switchVaultMode('table');
    } else if (initView === 'compare') {
        switchMainView('compare');
        const deck1 = urlParams.get('deck1');
        if (deck1) {
            selectedDeckIds.add(parseInt(deck1, 10));
            const deck2 = urlParams.get('deck2');
            if (deck2) selectedDeckIds.add(parseInt(deck2, 10));
            syncDropdownsWithSelection();
            updateSelectedCountUI();
        }
        if (selectedDeckIds.size >= 2) {
            runActiveComparison();
        }
    } else if (initView === 'analytics') {
        switchMainView('analytics');
    } else if (initView === 'ingest') {
        switchMainView('ingest');
    } else if (initView === 'inspector') {
        const available = (currentVaultDecks && currentVaultDecks.length > 0) ? currentVaultDecks : (allDecks || []);
        if (available && available.length > 0) {
            await loadSavedDeckIntoInspector(available[0].id, initTab);
        } else {
            displayDeckInInspector(null);
        }
    } else if (!currentVaultDecks || currentVaultDecks.length === 0) {
        switchMainView('ingest');
    }

    // Background fetch to ensure latest syncs are loaded
    await refreshVaultList();

    // Auto-update responsive card view on window resize
    window.addEventListener('resize', () => {
        if (currentCardViewMode === 'auto') {
            applyCardViewMode();
        }
    });
});

// Update Deck Selector Dropdowns
function updateDeckDropdowns(decks) {
    const selects = [
        document.getElementById('quickDeckSelect'),
        document.getElementById('inspectorDeckSelect'),
        document.getElementById('compareSelect1'),
        document.getElementById('compareSelect2'),
        document.getElementById('compareSelect3'),
        document.getElementById('compareSelect4')
    ];
    selects.forEach((sel, idx) => {
        if (!sel) return;
        const currentVal = sel.value;
        const defaultLabel = idx === 0 ? '⚡ Quick Select Deck...' : 
                             idx === 1 ? '⚡ Select Deck...' : 
                             idx === 2 ? '-- Choose Deck 1 --' : 
                             idx === 3 ? '-- Choose Deck 2 --' : 
                             idx === 4 ? '-- None (2-Way) --' : '-- None (3-Way) --';
        sel.innerHTML = `<option value="">${defaultLabel}</option>` + (decks || []).map(d => {
            const cmdr = d.commander_name ? ` (${escapeHtml(d.commander_name)})` : '';
            return `<option value="${d.id}">${escapeHtml(d.deck_name)}${cmdr}</option>`;
        }).join('');
        if (currentVal) sel.value = currentVal;
    });
}

// Vault Mode Switcher: Grid vs Comparative Table
function switchVaultMode(mode) {
    currentVaultMode = mode;
    const gridContainer = document.getElementById('vaultGridContainer');
    const tableContainer = document.getElementById('vaultTableContainer');
    const gridBtn = document.getElementById('vaultModeGridBtn');
    const tableBtn = document.getElementById('vaultModeTableBtn');
    const tableControls = document.getElementById('tableActionControls');

    if (mode === 'table') {
        if (gridContainer) gridContainer.classList.add('hidden');
        if (tableContainer) tableContainer.classList.remove('hidden');
        if (gridBtn) gridBtn.className = "px-2 sm:px-2.5 py-1 text-[10px] sm:text-xs font-mono rounded-none text-slate-400 hover:text-white transition cursor-pointer flex items-center gap-1";
        if (tableBtn) tableBtn.className = "px-2 sm:px-2.5 py-1 text-[10px] sm:text-xs font-mono rounded-none bg-cyan-500 text-slate-950 font-bold transition shadow-sm cursor-pointer flex items-center gap-1";
        if (tableControls) tableControls.classList.remove('hidden');
        renderMatrixTable(filteredDecks);
    } else {
        if (gridContainer) gridContainer.classList.remove('hidden');
        if (tableContainer) tableContainer.classList.add('hidden');
        if (gridBtn) gridBtn.className = "px-2 sm:px-2.5 py-1 text-[10px] sm:text-xs font-mono rounded-none bg-cyan-500 text-slate-950 font-bold transition shadow-sm cursor-pointer flex items-center gap-1";
        if (tableBtn) tableBtn.className = "px-2 sm:px-2.5 py-1 text-[10px] sm:text-xs font-mono rounded-none text-slate-400 hover:text-white transition cursor-pointer flex items-center gap-1";
        if (tableControls) tableControls.classList.add('hidden');
        renderVaultGrid(filteredDecks);
    }
}

// View Navigation Switcher
function switchMainView(viewName) {
    const views = {
        vault: document.getElementById('deckVaultView'),
        inspector: document.getElementById('deckInspectorView'),
        compare: document.getElementById('fleetCompareView'),
        analytics: document.getElementById('fleetAnalyticsView'),
        ingest: document.getElementById('deckIngestView')
    };

    const buttons = {
        vault: document.getElementById('viewVaultBtn'),
        inspector: document.getElementById('viewInspectorBtn'),
        compare: document.getElementById('viewCompareBtn'),
        analytics: document.getElementById('viewAnalyticsBtn'),
        ingest: document.getElementById('viewIngestBtn')
    };

    // Hide all views
    Object.values(views).forEach(v => {
        if (v) v.classList.add('hidden');
    });

    // Reset buttons to inactive styling
    Object.entries(buttons).forEach(([k, b]) => {
        if (!b) return;
        b.className = "px-2 sm:px-3.5 py-1.5 rounded-none text-[9px] xs:text-[10px] sm:text-xs font-mono font-bold uppercase transition-all text-slate-400 hover:text-white cursor-pointer flex items-center justify-center gap-1 sm:gap-1.5 flex-1 sm:flex-initial whitespace-nowrap";
    });

    // Activate the requested view
    const targetView = views[viewName] || views.vault;
    if (targetView) targetView.classList.remove('hidden');

    const targetBtn = buttons[viewName] || buttons.vault;
    if (targetBtn) {
        targetBtn.className = "px-2 sm:px-3.5 py-1.5 rounded-none text-[9px] xs:text-[10px] sm:text-xs font-mono font-bold uppercase transition-all bg-cyan-500 text-slate-950 shadow-md cursor-pointer flex items-center justify-center gap-1 sm:gap-1.5 flex-1 sm:flex-initial whitespace-nowrap";
    }

    if (viewName === 'vault') {
        if (currentVaultMode === 'table') {
            renderMatrixTable(filteredDecks);
        } else {
            renderVaultGrid(filteredDecks);
        }
    } else if (viewName === 'inspector') {
        if (!activeDeck) {
            const available = (currentVaultDecks && currentVaultDecks.length > 0) ? currentVaultDecks : (allDecks || []);
            if (available && available.length > 0) {
                loadSavedDeckIntoInspector(available[0].id);
                return;
            } else {
                displayDeckInInspector(null);
            }
        }
    } else if (viewName === 'compare') {
        if (!currentComparison && selectedDeckIds.size >= 2) {
            runActiveComparison();
        }
    } else if (viewName === 'analytics') {
        renderFleetPositioningChart(allDecks, currentFleetChartMetric);
    }
}

// Backward Compatibility Alias for switchOverviewView
function switchOverviewView(view) {
    if (view === 'matrix') {
        switchMainView('vault');
        switchVaultMode('table');
    } else if (view === 'compare') {
        switchMainView('compare');
    } else if (view === 'analytics') {
        switchMainView('analytics');
    } else {
        switchMainView(view);
    }
}

// Ingest Tab Switcher
function switchIngestTab(tab) {
    currentIngestTab = tab;
    ['url', 'text', 'csv'].forEach(t => {
        const btn = document.getElementById(`tab-btn-${t}`);
        const content = document.getElementById(`tab-content-${t}`);
        if (btn && content) {
            if (t === tab) {
                btn.className = "px-3 py-1.5 text-xs font-mono font-semibold rounded-none bg-cyan-500 text-slate-950 transition cursor-pointer";
                content.classList.remove('hidden');
            } else {
                btn.className = "px-3 py-1.5 text-xs font-mono font-semibold rounded-none text-slate-400 hover:text-white transition cursor-pointer";
                content.classList.add('hidden');
            }
        }
    });
}

// Inspector Tabs
let currentInspectorTab = 'stats';

function switchInspectorTab(tab, options = {}) {
    const { skipScroll = false } = options;
    currentInspectorTab = tab || 'stats';
    const tabs = ['stats', 'cards', 'ai', 'upgrades', 'tactics'];
    tabs.forEach(t => {
        const btn = document.getElementById(`insp-tab-${t}`);
        const content = document.getElementById(`inspectorTabContent${t.charAt(0).toUpperCase() + t.slice(1)}`);
        if (btn && content) {
            if (t === currentInspectorTab) {
                btn.classList.add('text-cyan-400', 'border-cyan-400', 'bg-cyan-500/10');
                btn.classList.remove('text-slate-400', 'border-transparent');
                content.classList.remove('hidden');
            } else {
                btn.classList.remove('text-cyan-400', 'border-cyan-400', 'bg-cyan-500/10');
                btn.classList.add('text-slate-400', 'border-transparent');
                content.classList.add('hidden');
            }
        }
    });

    if (currentInspectorTab === 'cards') {
        applyCardViewMode();
    }

    // Smoothly dock sticky tab bar below the top navigation bar so the user immediately sees the tab content
    if (!skipScroll) {
        const tabContainer = document.getElementById('inspectorTabBarContainer');
        if (tabContainer) {
            const navHeight = window.innerWidth >= 640 ? 64 : 56;
            const containerTop = tabContainer.getBoundingClientRect().top + window.scrollY;
            window.scrollTo({
                top: Math.max(0, containerTop - navHeight - 6),
                behavior: 'smooth'
            });
        }
    }

    if (currentInspectorTab === 'upgrades' && activeDeck && activeDeck.id) {
        loadDualTierUpgrades(activeDeck.id, { force: false });
    }

    if (currentInspectorTab === 'tactics' && activeDeck && activeDeck.id) {
        loadDeckTactics(activeDeck.id);
    }
}

// Render Saved Decks Vault Grid
function renderVaultGrid(decks) {
    const grid = document.getElementById('vaultGrid');
    const emptyState = document.getElementById('vaultEmptyState');
    const countElem = document.getElementById('savedDecksCount');
    if (countElem) countElem.innerText = (decks || []).length;

    if (!decks || decks.length === 0) {
        if (grid) grid.innerHTML = '';
        if (emptyState) emptyState.classList.remove('hidden');
        return;
    }

    if (emptyState) emptyState.classList.add('hidden');
    if (!grid) return;

    grid.innerHTML = decks.map(d => {
        const stats = d.stats || {};
        const rawVal = (d.total_value !== null && d.total_value !== undefined) ? d.total_value : (stats.total_value || 0);
        const val = parseFloat(rawVal).toFixed(2);
        const rawCmc = (d.avg_cmc !== null && d.avg_cmc !== undefined) ? d.avg_cmc : (stats.avg_cmc || 0);
        const cmc = parseFloat(rawCmc).toFixed(2);
        const art = d.commander_art || 'https://cards.scryfall.io/art_crop/front/b/4/b43e23b4-e699-4524-b6a5-30b14c330f60.jpg';
        const hasAi = d.has_ai_analysis;
        const pwr = d.power_level ? d.power_level.toFixed(1) : null;
        const rawDate = d.updated_at || d.created_at || (stats && stats.last_synced_at);
        const relDate = formatRelativeDate(rawDate);

        const colorPipsHtml = (d.color_identity || stats.color_identity || []).map(c => {
            const colors = { 'W': '#FFFBD5', 'U': '#0E68AB', 'B': '#150B00', 'R': '#D3202A', 'G': '#00733E' };
            return `<span class="inline-block w-2.5 h-2.5 rounded-full border border-[#263245]" style="background-color:${colors[c] || '#888'}"></span>`;
        }).join('');

        return `
            <div onclick="loadSavedDeckIntoInspector(${d.id})" class="bg-[#10141D] border border-[#263245] hover:border-cyan-500/60 rounded-none overflow-hidden shadow-xl hover:shadow-cyan-500/10 transition duration-200 group flex flex-col justify-between cursor-pointer transform hover:-translate-y-0.5">
                <div>
                    <!-- Commander Portrait Header -->
                    <div class="relative h-24 sm:h-32 overflow-hidden bg-slate-900">
                        <img src="${art}" alt="${escapeHtml(d.deck_name)}" loading="lazy" decoding="async" class="w-full h-full object-cover object-center group-hover:scale-105 transition duration-300">
                        <div class="absolute inset-0 bg-gradient-to-t from-[#10141D] via-[#10141D]/40 to-transparent pointer-events-none"></div>
                        
                        <!-- Badges -->
                        <div class="absolute top-2.5 left-2.5 right-2.5 flex items-center justify-between gap-1.5">
                            <div class="flex items-center gap-1.5">
                                <span class="px-2 py-0.5 rounded-none bg-slate-950/80 border border-[#263245] text-[10px] font-mono text-slate-300 font-bold">${d.total_cards || 100} Cards</span>
                                ${d.is_pauper ? `<span class="px-1.5 py-0.5 rounded-none bg-purple-900/90 border border-purple-500/80 text-[10px] font-mono font-black text-purple-200">PDH</span>` : `<span class="px-1.5 py-0.5 rounded-none bg-slate-900/80 border border-[#263245] text-[10px] font-mono text-slate-400">EDH</span>`}
                                ${hasAi ? `<span class="px-2 py-0.5 rounded-none bg-cyan-500/90 text-slate-950 text-[10px] font-mono font-black cursor-help" data-tooltip-key="power_level">AI: ${pwr || '7.5'} ⓘ</span>` : `<span class="px-2 py-0.5 rounded-none bg-slate-800/80 text-slate-400 text-[10px] font-mono">Stats Ready</span>`}
                            </div>
                            ${relDate ? `<span class="px-2 py-0.5 rounded-none bg-slate-950/80 border border-[#263245] text-[10px] font-mono text-slate-400 font-medium" title="Last Updated: ${escapeHtml(rawDate)}">📅 ${relDate}</span>` : ''}
                        </div>

                        <div class="absolute bottom-2 left-3 right-3 flex items-center justify-between">
                            <span class="text-xs text-cyan-300 font-mono truncate font-semibold">${escapeHtml(d.commander_name || 'Commander')}</span>
                            <div class="flex items-center gap-0.5 bg-slate-950/80 px-1.5 py-0.5 rounded-none border border-[#263245] cursor-help" data-tooltip-key="col_colors">
                                ${colorPipsHtml || '<span class="text-[9px] text-slate-500 font-mono">Colorless</span>'}
                            </div>
                        </div>
                    </div>

                    <!-- Body -->
                    <div class="p-4 space-y-3">
                        <h4 class="text-sm font-bold text-white font-mono uppercase tracking-wide truncate group-hover:text-cyan-300 transition">${escapeHtml(d.deck_name)}</h4>
                        
                        <!-- Telemetry Grid -->
                        <div class="grid grid-cols-2 gap-2 bg-[#1B2230]/60 p-2 rounded-none border border-[#263245] font-mono text-xs">
                            <div class="cursor-help" data-tooltip-key="tcg_value">
                                <span class="text-[10px] text-slate-400 uppercase block">TCG Value ⓘ</span>
                                <span class="text-emerald-400 font-bold">$${val}</span>
                            </div>
                            <div class="cursor-help" data-tooltip-key="amv">
                                <span class="text-[10px] text-slate-400 uppercase block">Avg Nonland CMC ⓘ</span>
                                <span class="text-amber-400 font-bold">${cmc}</span>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Card Actions Footer -->
                <div class="p-3 sm:p-4 pt-0 grid grid-cols-2 sm:grid-cols-4 gap-1.5 border-t border-[#263245] mt-2">
                    <button onclick="event.stopPropagation(); loadSavedDeckIntoInspector(${d.id})" class="px-2 py-1.5 sm:py-2 bg-cyan-500/10 hover:bg-cyan-500 text-cyan-400 hover:text-slate-950 font-mono text-[9px] xs:text-[10px] sm:text-[11px] font-bold uppercase rounded-none border border-cyan-500/30 hover:border-cyan-500 transition text-center flex items-center justify-center gap-1 cursor-pointer" title="Deep-dive deck analysis">
                        <svg class="w-3 h-3 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
                        <span>Inspect</span>
                    </button>
                    <button onclick="event.stopPropagation(); loadSavedDeckIntoInspector(${d.id}, 'upgrades')" class="px-2 py-1.5 sm:py-2 bg-emerald-500/10 hover:bg-emerald-500 text-emerald-400 hover:text-slate-950 font-mono text-[9px] xs:text-[10px] sm:text-[11px] font-bold uppercase rounded-none border border-emerald-500/30 hover:border-emerald-500 transition text-center flex items-center justify-center gap-1 cursor-pointer" title="View deck upgrades & binder swaps">
                        <svg class="w-3 h-3 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/></svg>
                        <span>Upgrade</span>
                    </button>
                    <button onclick="event.stopPropagation(); syncSavedDeck(${d.id})" class="px-2 py-1.5 sm:py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white font-mono text-[9px] xs:text-[10px] sm:text-[11px] font-bold uppercase rounded-none border border-[#263245] hover:border-slate-600 transition text-center flex items-center justify-center gap-1 cursor-pointer" title="Re-sync deck list from ManaBox">
                        <svg class="w-3 h-3 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                        <span>Sync</span>
                    </button>
                    <button onclick="event.stopPropagation(); handleDeleteDeckClick(${d.id})" class="px-2 py-1.5 sm:py-2 bg-rose-500/10 hover:bg-rose-500 text-rose-400 hover:text-white font-mono text-[9px] xs:text-[10px] sm:text-[11px] font-bold uppercase rounded-none border border-rose-500/30 hover:border-rose-500 transition text-center flex items-center justify-center gap-1 cursor-pointer" title="Delete deck from Vault">
                        <svg class="w-3 h-3 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                        <span>Delete</span>
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

// Load Deck into Inspector
async function loadSavedDeckIntoInspector(deckId, targetTab = null) {
    try {
        const resp = await fetch(`/api/deck/history/${deckId}`);
        if (!resp.ok) throw new Error("Failed to load saved deck");
        const deck = await resp.json();
        try {
            const newUrl = new URL(window.location);
            newUrl.searchParams.set('deck_id', deckId);
            if (targetTab) newUrl.searchParams.set('tab', targetTab);
            window.history.replaceState(null, '', newUrl.toString());
        } catch (_) {}
        displayDeckInInspector(deck);
        const bDeck = document.getElementById('breadcrumbDeckName');
        if (bDeck) bDeck.innerText = deck.deck_name || 'Commander Deck';
        if (targetTab) {
            setTimeout(() => switchInspectorTab(targetTab), 50);
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    }
}
async function _legacyLoadSavedDeckIntoInspector(deckId) {
    try {
        const resp = await fetch(`/api/deck/history/${deckId}`);
        if (!resp.ok) throw new Error("Failed to load saved deck");
        const deck = await resp.json();
        try {
            const newUrl = new URL(window.location);
            newUrl.searchParams.set('deck_id', deckId);
            window.history.replaceState(null, '', newUrl.toString());
        } catch (_) {}
        displayDeckInInspector(deck);
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    }
}

let pipSourceRadarChartInstance = null;

// Helper for safe text setting
function safeSetText(id, text) {
    const el = document.getElementById(id);
    if (el) el.innerText = text;
}

// Display Deck in Inspector
function displayDeckInInspector(deck, keepCurrentTab = false) {
    const emptyState = document.getElementById('inspectorEmptyState');
    const banner = document.getElementById('deckHeaderBanner');
    const tabBar = document.getElementById('inspectorTabBarContainer');
    const tabContents = [
        document.getElementById('inspectorTabContentStats'),
        document.getElementById('inspectorTabContentCards'),
        document.getElementById('inspectorTabContentAi'),
        document.getElementById('inspectorTabContentUpgrades'),
        document.getElementById('inspectorTabContentTactics')
    ];

    if (!deck) {
        activeDeck = null;
        if (emptyState) emptyState.classList.remove('hidden');
        if (banner) banner.classList.add('hidden');
        if (tabBar) tabBar.classList.add('hidden');
        tabContents.forEach(tc => { if (tc) tc.classList.add('hidden'); });
        switchMainView('inspector');
        return;
    }

    if (emptyState) emptyState.classList.add('hidden');
    if (banner) banner.classList.remove('hidden');
    if (tabBar) tabBar.classList.remove('hidden');

    activeDeck = deck;
    switchMainView('inspector');

    if (deck.id) {
        const quickSel = document.getElementById('quickDeckSelect');
        if (quickSel) quickSel.value = deck.id;
        const inspSel = document.getElementById('inspectorDeckSelect');
        if (inspSel) inspSel.value = deck.id;
    }

    const stats = deck.stats || {};
    const cards = deck.cards_data || deck.cards || [];
    const analysis = deck.analysis || {};

    // Populate Header
    safeSetText('inspectorDeckName', deck.deck_name || "Commander Deck");
    safeSetText('inspectorCmdrName', deck.commander_name || (deck.commander ? (Array.isArray(deck.commander) ? deck.commander.join(', ') : deck.commander) : "Commander"));
    safeSetText('inspectorCardCount', `${deck.total_cards || cards.length} Cards`);
    safeSetText('tabCardCount', deck.total_cards || cards.length);
    document.querySelectorAll('.tabCardCountMobile').forEach(el => el.innerText = deck.total_cards || cards.length);
    safeSetText('inspectorTotalValue', `$${(deck.total_value || stats.total_value || 0).toFixed(2)}`);
    
    // Format Badge & Label
    const isPauper = Boolean(deck.is_pauper || (deck.deck_format && deck.deck_format.toLowerCase().includes('pauper')));
    const formatBadge = document.getElementById('inspectorFormatBadge');
    const formatLabel = document.getElementById('inspectorFormatLabel');
    if (formatLabel) {
        formatLabel.innerText = isPauper ? 'PDH' : 'EDH';
    }
    if (formatBadge) {
        if (isPauper) {
            formatBadge.className = "text-[10px] px-2 py-0.5 rounded-none font-mono font-bold uppercase transition flex items-center gap-1 cursor-pointer bg-purple-950/90 border border-purple-500/70 text-purple-300 hover:bg-purple-900 shadow-sm";
            formatBadge.title = "Format: Pauper Commander (PDH). Click to switch to Regular Commander (EDH)";
        } else {
            formatBadge.className = "text-[10px] px-2 py-0.5 rounded-none font-mono font-bold uppercase transition flex items-center gap-1 cursor-pointer bg-slate-800 border border-[#263245] text-slate-300 hover:border-cyan-400 hover:text-white";
            formatBadge.title = "Format: Regular Commander (EDH). Click to switch to Pauper Commander (PDH)";
        }
    }

    // Update Pauper Upgrade Notice Banner in Upgrades Tab
    const pauperUpgradeBanner = document.getElementById('pauperUpgradeBanner');
    if (pauperUpgradeBanner) {
        if (isPauper) {
            pauperUpgradeBanner.classList.remove('hidden');
        } else {
            pauperUpgradeBanner.classList.add('hidden');
        }
    }

    // Relative Date and Header Actions
    const rawDate = deck.updated_at || deck.created_at || (stats && stats.last_synced_at);
    const relDate = formatRelativeDate(rawDate);
    const dateBadge = document.getElementById('inspectorDateBadge');
    if (dateBadge) {
        dateBadge.innerText = relDate ? `📅 Updated: ${relDate}` : '📅 Updated: Recent';
    }

    const inspCompareBtn = document.getElementById('inspectorCompareBtn');
    if (inspCompareBtn) {
        inspCompareBtn.onclick = launchCompareFromActiveDeck;
    }

    const nameBadge = document.getElementById('inspectorActiveNameBadge');
    if (nameBadge) {
        nameBadge.innerText = deck.deck_name || 'Active';
        nameBadge.classList.remove('hidden');
    }
    const inspNavBtn = document.getElementById('viewInspectorBtn');
    if (inspNavBtn) {
        inspNavBtn.classList.remove('hidden');
    }

    // Nonland AMV Badge with Color Coding (Green <= 2.8, Amber 2.9-3.4, Red >= 3.5)
    const amv = deck.avg_cmc || stats.nonland_amv || stats.avg_cmc || 0;
    const amvElem = document.getElementById('inspectorAvgCmc');
    const amvContainer = document.getElementById('inspectorAmvContainer');
    if (amvElem) amvElem.innerText = amv.toFixed(2);
    safeSetText('curveAvgCmc', `Avg CMC: ${amv.toFixed(2)}`);

    if (amvElem) {
        if (amv <= 2.8) {
            amvElem.className = "text-xs sm:text-sm font-black text-emerald-400 font-mono";
            if (amvContainer) amvContainer.className = "bg-emerald-500/10 border border-emerald-500/40 rounded-none px-2 py-1.5 text-center min-w-0 flex-1 sm:flex-initial";
        } else if (amv <= 3.4) {
            amvElem.className = "text-xs sm:text-sm font-black text-amber-400 font-mono";
            if (amvContainer) amvContainer.className = "bg-amber-500/10 border border-amber-500/40 rounded-none px-2 py-1.5 text-center min-w-0 flex-1 sm:flex-initial";
        } else {
            amvElem.className = "text-xs sm:text-sm font-black text-rose-400 font-mono";
            if (amvContainer) amvContainer.className = "bg-rose-500/10 border border-rose-500/40 rounded-none px-2 py-1.5 text-center min-w-0 flex-1 sm:flex-initial";
        }
    }

    // Telemetry HUD Counters
    safeSetText('inspectorFastRamp', stats.fast_ramp_count || 0);
    safeSetText('inspectorInstantSpeed', `${stats.instant_speed_ratio || 0}%`);
    safeSetText('inspectorTaplandPenalty', `${stats.tapland_penalty_index || 0}%`);
    safeSetText('inspectorEngineDraw', stats.draw_engine_count || 0);

    // Detailed Profile Widgets
    safeSetText('velFastRamp', stats.fast_ramp_count || 0);
    safeSetText('velStdRamp', stats.standard_ramp_count || 0);
    safeSetText('velDorkRock', stats.dork_or_rock_count || (stats.ramp_type_breakdown && stats.ramp_type_breakdown.dork_or_rock) || 0);
    safeSetText('velLandFetch', stats.land_fetch_count || (stats.ramp_type_breakdown && stats.ramp_type_breakdown.land_fetch) || 0);
    safeSetText('velTreasure', stats.treasure_count || (stats.ramp_type_breakdown && stats.ramp_type_breakdown.treasure) || 0);
    safeSetText('velLandsSummary', `${stats.land_count || (stats.type_counts && stats.type_counts.Lands) || 0} / ${stats.taplands_count || 0} tap`);
    safeSetText('velTaplandPenalty', `${stats.tapland_penalty_index || 0}%`);

    safeSetText('intTargeted', stats.targeted_removal_count || 0);
    safeSetText('intWipes', stats.board_wipe_count || 0);
    safeSetText('intEfficiency', `${(stats.removal_mana_efficiency || 0).toFixed(2)} CMC`);
    safeSetText('intInstantSpeed', `${stats.instant_speed_ratio || 0}%`);

    safeSetText('caEngine', stats.draw_engine_count || 0);
    safeSetText('caBurstCantrip', `${stats.draw_burst_count || 0} burst / ${stats.draw_cantrip_count || 0} cantrip`);
    safeSetText('caTutorGen', stats.tutor_general_count || 0);
    safeSetText('caTutorLand', stats.tutor_land_count || 0);

    // Make Telemetry Counters Clickable for Drilldown into Card Registry
    const attachDrilldown = (elemId, type, val, label) => {
        const el = document.getElementById(elemId);
        if (el) {
            el.classList.add('cursor-pointer', 'hover:underline', 'transition');
            el.title = `Click to filter Card Registry by ${label}`;
            el.onclick = (e) => {
                e.stopPropagation();
                drillDownToCardRegistry(type, val, label);
            };
        }
    };
    attachDrilldown('inspectorFastRamp', 'role', 'fast_ramp', 'Fast Mana / Ramp');
    attachDrilldown('velFastRamp', 'role', 'fast_ramp', 'Fast Mana / Ramp');
    attachDrilldown('velStdRamp', 'role', 'std_ramp', 'Standard Ramp');
    attachDrilldown('inspectorEngineDraw', 'role', 'engine_draw', 'Engine Draw');
    attachDrilldown('caEngine', 'role', 'engine_draw', 'Engine Draw');
    attachDrilldown('intTargeted', 'role', 'targeted_removal', 'Targeted Removal');
    attachDrilldown('intWipes', 'role', 'board_wipe', 'Board Wipes');
    attachDrilldown('caBurstCantrip', 'role', 'cantrip', 'Burst / Cantrips');
    attachDrilldown('caTutorGen', 'role', 'tutor', 'Tutors');
    
    // Commander Art & Background
    const art = deck.commander_art || (cards[0] && cards[0].image_uri) || 'https://cards.scryfall.io/art_crop/front/b/4/b43e23b4-e699-4524-b6a5-30b14c330f60.jpg';
    const cmdrArt = document.getElementById('inspectorCmdrArt');
    if (cmdrArt) cmdrArt.src = art;
    const bannerBg = document.getElementById('commanderBannerBg');
    if (bannerBg) bannerBg.style.backgroundImage = `url('${art}')`;

    // Color Pips
    const pipsContainer = document.getElementById('cmdrColorPips');
    if (pipsContainer) {
        const colors = { 'W': '#FFFBD5', 'U': '#0E68AB', 'B': '#150B00', 'R': '#D3202A', 'G': '#00733E' };
        pipsContainer.innerHTML = (deck.color_identity || stats.color_identity || []).map(c => 
            `<span class="inline-block w-2.5 h-2.5 rounded-full border border-[#263245]" style="background-color:${colors[c] || '#888'}"></span>`
        ).join('');
    }

    // Power Bracket & Archetype
    const archetypeVal = deck.archetype || stats.archetype || (analysis && analysis.archetype) || "Midrange / Engine";
    safeSetText('inspectorArchetype', archetypeVal);

    const aiTabBadge = document.getElementById('aiTabBadge');
    const aiUnanalyzedBanner = document.getElementById('aiUnanalyzedBanner');
    const aiAnalyzedContent = document.getElementById('aiAnalyzedContent');

    if (deck.has_ai_analysis && analysis && Object.keys(analysis).length > 0) {
        const numPower = parseFloat(deck.power_level !== undefined && deck.power_level !== null ? deck.power_level : (analysis.estimated_power_level || 7.5)) || 7.5;
        safeSetText('inspectorPowerVal', `${numPower.toFixed(1)} / 10`);
        safeSetText('inspectorPowerBracket', deck.power_bracket || analysis.power_bracket || "Optimized (7-8)");
        if (aiTabBadge) {
            aiTabBadge.innerText = "Analyzed";
            aiTabBadge.className = "text-[9px] px-1.5 py-0.2 rounded-none bg-cyan-500/20 text-cyan-400 border border-cyan-500/30";
        }
        if (aiUnanalyzedBanner) aiUnanalyzedBanner.classList.add('hidden');
        if (aiAnalyzedContent) aiAnalyzedContent.classList.remove('hidden');

        const rerunSelect = document.getElementById('rerunDeckAiModel');
        if (rerunSelect && (deck.model_used || analysis._model_used)) {
            rerunSelect.value = deck.model_used || analysis._model_used;
        }

        renderAiAnalysis(analysis);
        currentCardSortKey = 'rating_desc';
    } else {
        safeSetText('inspectorPowerVal', `-- / 10`);
        safeSetText('inspectorPowerBracket', "Telemetry Ready");
        if (aiTabBadge) {
            aiTabBadge.innerText = "Ready";
            aiTabBadge.className = "text-[9px] px-1.5 py-0.2 rounded-none bg-amber-500/20 text-amber-400 border border-amber-500/30";
        }
        if (aiUnanalyzedBanner) aiUnanalyzedBanner.classList.remove('hidden');
        if (aiAnalyzedContent) aiAnalyzedContent.classList.add('hidden');
        currentCardSortKey = 'cmc_asc';
    }

    const sortSelect = document.getElementById('cardSortSelect');
    if (sortSelect) sortSelect.value = currentCardSortKey;

    // Render Mana Curve & Type Breakdowns
    renderManaCurve(stats.cmc_curve || {});
    renderTypeBreakdown(stats.type_counts || {}, deck.total_cards || cards.length);

    // Render Pip Demand vs Source Supply Radar Chart & Cards
    renderPipSourceRadar(stats.pip_breakdown || {});
    renderPipSourceCards(stats.pip_breakdown || {});

    // Render 11-Metric Advanced Telemetry Stations
    renderSpeedAndManaTelemetry(stats);
    renderInteractionDefenseTelemetry(stats);
    renderEngineAndSynergyTelemetry(stats);
    renderEndgameAndWinconTelemetry(stats);

    // Render Full Card Table & Mobile Tiles
    renderCardRegistryTable(cards);

    // Render Format Rules Compliance Evaluation
    renderRulesEvaluation(deck);

    // Load EDHREC Metadata, Primers & Combos
    loadEdhrecMetadataForDeck(deck);

    // Render Commander Tactics badge & stale telemetry
    updateTacticsBadgeAndBanner(deck);

    if (!keepCurrentTab) {
        switchInspectorTab('stats', { skipScroll: true });
    } else if (currentInspectorTab) {
        switchInspectorTab(currentInspectorTab, { skipScroll: true });
    }
}

// Render Speed & Opening Mana Telemetry (Land Drops, Keepability & Commander Cast)
function renderSpeedAndManaTelemetry(stats) {
    const ldp = stats.land_drop_probabilities || {};
    const eff = ldp.effective_with_mulligan || {};
    const cantrips = ldp.cheap_cantrip_count || stats.draw_cantrip_count || 0;
    safeSetText('speedCantripCount', `${cantrips} Cantrips (CMC <= 2)`);

    const t1Raw = ldp.turn_1 || 0;
    const t1Eff = eff.turn_1 || t1Raw;
    safeSetText('landProbT1', `${t1Raw}% (eff ${t1Eff}%)`);
    const b1 = document.getElementById('landProbT1Bar');
    if (b1) b1.style.width = `${Math.min(100, t1Eff)}%`;

    const t2Raw = ldp.turn_2 || 0;
    const t2Eff = eff.turn_2 || t2Raw;
    safeSetText('landProbT2', `${t2Raw}% (eff ${t2Eff}%)`);
    const b2 = document.getElementById('landProbT2Bar');
    if (b2) b2.style.width = `${Math.min(100, t2Eff)}%`;

    const t3Raw = ldp.turn_3 || 0;
    const t3Eff = eff.turn_3 || t3Raw;
    safeSetText('landProbT3', `${t3Raw}% (eff ${t3Eff}%)`);
    const b3 = document.getElementById('landProbT3Bar');
    if (b3) b3.style.width = `${Math.min(100, t3Eff)}%`;

    const t4Raw = ldp.turn_4 || 0;
    const t4Eff = eff.turn_4 || t4Raw;
    safeSetText('landProbT4', `${t4Raw}% (eff ${t4Eff}%)`);
    const b4 = document.getElementById('landProbT4Bar');
    if (b4) b4.style.width = `${Math.min(100, t4Eff)}%`;

    // Keepability
    const keep = stats.opening_hand_keepability || {};
    safeSetText('keepEffectiveRate', `${keep.effective_keep_rate || 0}%`);
    safeSetText('keepNaturalRate', `${keep.natural_keep_rate || 0}%`);
    safeSetText('keepAvgLands', `Avg ${keep.avg_lands_in_hand || 0} Lands`);
    const bk = keep.breakdown || {};
    safeSetText('keepOptimalPct', `${bk.optimal || 0}%`);
    safeSetText('keepWorkablePct', `${bk.workable || 0}%`);
    safeSetText('keepRiskyPct', `${bk.risky || 0}%`);
    safeSetText('keepUnkeepablePct', `${bk.unkeepable || 0}%`);

    // Commander Cast
    const cmdr = stats.earliest_commander_cast || {};
    safeSetText('cmdrMedianCastTurn', `Turn ${cmdr.median_cast_turn || 4}`);
    safeSetText('cmdrCastEarliest', `Earliest T${cmdr.earliest_possible_turn || 2}`);
    safeSetText('cmdrAvgCastTurn', cmdr.avg_cast_turn || 4.0);
    safeSetText('cmdrColorBottleneck', cmdr.color_bottleneck || 'None');

    const distContainer = document.getElementById('cmdrCastDistBars');
    if (distContainer && cmdr.turn_distribution) {
        const td = cmdr.turn_distribution;
        const colors = ['bg-emerald-400', 'bg-cyan-400', 'bg-blue-400', 'bg-amber-400', 'bg-rose-400'];
        const keys = ['turn_2', 'turn_3', 'turn_4', 'turn_5', 'turn_6_plus'];
        distContainer.innerHTML = keys.map((k, idx) => {
            const pct = td[k] || 0;
            return `<div class="${colors[idx]} h-full rounded-none transition-all duration-500" style="width:${Math.max(2, pct)}%" title="${k.replace('_', ' ')}: ${pct}%"></div>`;
        }).join('');
    }
}

// Render Interaction & Defense Matrix (Coverage, Protection & Instant Holdout)
function renderInteractionDefenseTelemetry(stats) {
    const tc = stats.threat_coverage || {};
    const cat = tc.categories || {};
    const grid = document.getElementById('threatCoverageGrid');
    if (grid) {
        const typeIcons = {
            'creatures': '⚔️ Creatures',
            'artifacts': '🏺 Artifacts',
            'enchantments': '✨ Enchantments',
            'planeswalkers': '👁️ Walkers',
            'graveyards': '🪦 Graveyards',
            'lands': '🏔️ Lands',
            'spells': '⚡ Spells (Stack)',
        };
        const order = ['creatures', 'artifacts', 'enchantments', 'planeswalkers', 'graveyards', 'lands', 'spells'];
        grid.innerHTML = order.map(k => {
            const item = cat[k] || { total: 0, instant: 0, sorcery: 0, cards: [] };
            const isZeroInstant = (item.instant === 0 && (k === 'artifacts' || k === 'enchantments'));
            const borderCls = isZeroInstant ? 'border-amber-500/40 bg-amber-500/5' : 'border-[#263245] bg-[#0B0F17]/80';
            return `
                <div class="p-2.5 rounded-none border ${borderCls} font-mono flex flex-col justify-between">
                    <div>
                        <div class="flex items-center justify-between text-[11px] font-bold text-slate-300">
                            <span>${typeIcons[k] || k}</span>
                            <span class="text-xs ${item.total > 0 ? 'text-cyan-400 font-black' : 'text-slate-500'}">${item.total}</span>
                        </div>
                        <div class="flex items-center gap-1.5 text-[9px] mt-1.5 text-slate-400">
                            <span class="px-1 py-0.2 rounded-none bg-cyan-500/10 text-cyan-300 font-bold">${item.instant} Inst</span>
                            <span class="px-1 py-0.2 rounded-none bg-slate-800 text-slate-400">${item.sorcery} Sorc</span>
                        </div>
                    </div>
                    ${item.cards.length > 0 ? `<div class="text-[9px] text-slate-500 truncate mt-1.5" title="${item.cards.join(', ')}">${item.cards.slice(0, 2).join(', ')}${item.cards.length > 2 ? '...' : ''}</div>` : `<div class="text-[9px] text-slate-600 italic mt-1.5">No answers</div>`}
                </div>
            `;
        }).join('');
    }

    const vulnBox = document.getElementById('threatVulnerabilitiesContainer');
    if (vulnBox) {
        if (tc.vulnerabilities && tc.vulnerabilities.length > 0) {
            vulnBox.classList.remove('hidden');
            vulnBox.innerHTML = `
                <div class="font-bold flex items-center gap-1.5 text-rose-400 text-[11px] mb-1">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
                    <span>Coverage Vulnerability Alerts:</span>
                </div>
                ${tc.vulnerabilities.map(v => `<div class="text-[10px] pl-4">&bull; ${v}</div>`).join('')}
            `;
        } else {
            vulnBox.classList.add('hidden');
        }
    }

    // Counter vs Protection
    const cp = stats.counter_vs_protection || {};
    safeSetText('cpCounterCount', cp.counterspell_count || 0);
    safeSetText('cpProtectionCount', cp.protection_count || 0);
    safeSetText('cpStanceBadge', cp.stance || 'Balanced');
    const pb = cp.protection_breakdown || {};
    const pbContainer = document.getElementById('cpBreakdownPills');
    if (pbContainer) {
        pbContainer.innerHTML = `
            <span>Hexproof: <b class="text-emerald-300 font-bold">${pb.hexproof_shroud || 0}</b></span>
            <span>Indestructible: <b class="text-cyan-300 font-bold">${pb.indestructible || 0}</b></span>
            <span>Phase: <b class="text-purple-300 font-bold">${pb.phase_out || 0}</b></span>
            <span>Flicker: <b class="text-amber-300 font-bold">${pb.flicker_bounce || 0}</b></span>
            <span>Ward: <b class="text-slate-300 font-bold">${pb.ward_prot || 0}</b></span>
        `;
    }

    // Instant Holdout
    const ho = stats.instant_mana_holdout || {};
    safeSetText('holdoutAvgValue', `${(ho.avg_holdout_cmc || 0).toFixed(2)} CMC`);
    safeSetText('holdoutRatingDesc', ho.rating || 'Average mana required to pass turn holding interaction.');
    const hoBadge = document.getElementById('holdoutRatingBadge');
    if (hoBadge) {
        hoBadge.innerText = `Holdout: ${(ho.avg_holdout_cmc || 0).toFixed(1)} CMC (${ho.rating ? ho.rating.split(' ')[0] : 'Normal'})`;
        const color = ho.rating_color || 'slate';
        hoBadge.className = `text-xs font-mono px-2.5 py-1 rounded-none bg-${color}-500/20 text-${color}-400 border border-${color}-500/30`;
    }
    const cb = ho.cmc_breakdown || {};
    safeSetText('holdoutCmc0', cb['0'] || 0);
    safeSetText('holdoutCmc1', cb['1'] || 0);
    safeSetText('holdoutCmc2', cb['2'] || 0);
    safeSetText('holdoutCmc3', cb['3'] || 0);
    safeSetText('holdoutCmc4', cb['4+'] || 0);
}

// Render Engine, Typal & Virtual Card Advantage Telemetry
function renderEngineAndSynergyTelemetry(stats) {
    // Engine Enabler vs Payoff
    const ep = stats.enabler_payoff || {};
    safeSetText('engineThemeBadge', ep.theme || 'Engine');
    safeSetText('engineRatioValue', `${(ep.ratio || 1.0).toFixed(1)} : 1`);
    safeSetText('engineEnablerCount', ep.enabler_count || 0);
    safeSetText('enginePayoffCount', ep.payoff_count || 0);
    const healthBox = document.getElementById('engineHealthBox');
    if (healthBox) {
        healthBox.innerText = `Engine: ${ep.health || 'Balanced'}`;
        const color = ep.status_color || 'slate';
        healthBox.className = `text-[11px] font-mono p-2 rounded-none bg-${color}-500/10 border border-${color}-500/30 text-${color}-300`;
    }

    // Typal / Kindred
    const typ = stats.typal_density || {};
    const typBadge = document.getElementById('typalDeckBadge');
    if (typBadge) {
        if (typ.is_typal_deck) {
            typBadge.innerText = `${typ.primary_type} Kindred`;
            typBadge.className = "text-[9px] px-1.5 py-0.5 rounded-none bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 font-mono font-bold";
        } else {
            typBadge.innerText = "Non-Typal";
            typBadge.className = "text-[9px] px-1.5 py-0.5 rounded-none bg-slate-800 text-slate-400 font-mono";
        }
    }
    safeSetText('typalPrimaryType', typ.primary_type || 'None');
    safeSetText('typalDeckPct', `${typ.total_typal_deck_pct || 0}% synergy`);
    safeSetText('typalCreatureCount', `${typ.matching_creatures_count || 0} (${typ.matching_creatures_pct || 0}%)`);
    safeSetText('typalSupportCount', `${typ.kindred_support_count || 0} cards`);
    const suppList = document.getElementById('typalSupportList');
    if (suppList) {
        if (typ.kindred_support_cards && typ.kindred_support_cards.length > 0) {
            suppList.innerText = `Support: ${typ.kindred_support_cards.join(', ')}`;
            suppList.title = typ.kindred_support_cards.join(', ');
        } else {
            suppList.innerText = "Support: None detected";
        }
    }

    // Virtual Card Advantage
    const vca = stats.virtual_card_advantage || {};
    safeSetText('virtualTotalAdvantage', `${vca.total_virtual_advantage || 0} Resources`);
    safeSetText('vcaPureDraw', vca.pure_draw || 0);
    safeSetText('vcaImpulseDraw', vca.impulse_draw || 0);
    safeSetText('vcaRecursion', vca.recursion || 0);
    safeSetText('vcaTutors', vca.tutors || 0);
    const vcaBox = document.getElementById('vcaRatingBox');
    if (vcaBox) {
        vcaBox.innerText = `Resource Depth: ${vca.resource_depth_rating || 'Moderate'}`;
        const color = vca.rating_color || 'emerald';
        vcaBox.className = `text-[11px] font-mono p-2 rounded-none bg-${color}-500/10 border border-${color}-500/30 text-${color}-300`;
    }
}

// Render Endgame & Win Conditions Telemetry
function renderEndgameAndWinconTelemetry(stats) {
    const wc = stats.win_conditions || {};
    const pw = wc.primary_wincon || {};
    const sw = wc.secondary_wincon || {};
    safeSetText('winconClockEstimate', `Clock: ${wc.clock_estimate || 'Turns 7-9'}`);
    safeSetText('primaryWinconName', pw.name || 'Combat Damage & Board Presence');
    safeSetText('primaryWinconConfidence', pw.confidence || 'Moderate');
    safeSetText('primaryWinconCards', pw.key_cards && pw.key_cards.length > 0 ? `Key pieces: ${pw.key_cards.join(', ')}` : 'Built on creature combat and board attrition.');

    safeSetText('secondaryWinconName', sw.name || 'Combat Fallback');
    safeSetText('secondaryWinconConfidence', sw.confidence || 'Incidental');
    safeSetText('secondaryWinconCards', sw.key_cards && sw.key_cards.length > 0 ? `Key pieces: ${sw.key_cards.join(', ')}` : 'Standard incremental damage.');

    // Mana Sinks
    const ms = stats.mana_sinks || {};
    safeSetText('manaSinkTotalBadge', `${ms.total_sinks || 0} Outlets`);
    const msBox = document.getElementById('manaSinkResilienceBox');
    if (msBox) {
        msBox.innerText = ms.late_game_resilience || 'Analyzing...';
        const color = ms.resilience_color || 'cyan';
        msBox.className = `text-xs font-mono p-2 rounded-none bg-${color}-500/10 border border-${color}-500/30 text-${color}-300`;
    }
    const sinkList = document.getElementById('manaSinkCardsList');
    if (sinkList) {
        if (ms.top_sinks && ms.top_sinks.length > 0) {
            sinkList.innerHTML = ms.top_sinks.map(s => 
                `<span class="text-[10px] font-mono px-2 py-0.5 rounded-none bg-slate-800 text-slate-300 border border-[#263245] cursor-pointer hover:border-cyan-400 hover:text-white transition"
                       data-img="${escapeHtml(resolveCardImageUri(s.name))}"
                       data-card-name="${escapeHtml(s.name)}"
                       onmouseenter="showCardHover(event, this)"
                       onmousemove="moveCardHover(event)"
                       onmouseleave="hideCardHover()"
                       onclick="showCardHover(event, this)"
                       title="${s.type} (${s.cmc} CMC)">${escapeHtml(s.name)} <span class="text-cyan-400 text-[9px]">${escapeHtml(s.type)}</span></span>`
            ).join('');
        } else {
            sinkList.innerHTML = `<span class="text-[10px] font-mono text-slate-500 italic">No repeatable mana sinks detected.</span>`;
        }
    }
    const tb = ms.type_breakdown || {};
    safeSetText('sinkCountX', tb.x_spell || 0);
    safeSetText('sinkCountAct', tb.activated_ability || 0);
    safeSetText('sinkCountLand', tb.utility_land || 0);
}

// Render Pip vs Source Radar Chart
function renderPipSourceRadar(pipBreakdown) {
    const canvas = document.getElementById('pipSourceRadarChart');
    if (!canvas || typeof Chart === 'undefined') return;
    const ctx = canvas.getContext('2d');
    if (pipSourceRadarChartInstance) {
        pipSourceRadarChartInstance.destroy();
    }

    const labels = ['White (W)', 'Blue (U)', 'Black (B)', 'Red (R)', 'Green (G)', 'Colorless (C)'];
    const keys = ['W', 'U', 'B', 'R', 'G', 'C'];
    const demandData = keys.map(k => (pipBreakdown[k] ? pipBreakdown[k].pip_demand_pct : 0));
    const supplyData = keys.map(k => (pipBreakdown[k] ? pipBreakdown[k].source_supply_pct : 0));

    pipSourceRadarChartInstance = new Chart(ctx, {
        type: 'radar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: '% Pip Demand',
                    data: demandData,
                    backgroundColor: 'rgba(6, 182, 212, 0.25)',
                    borderColor: '#06b6d4',
                    borderWidth: 2,
                    pointBackgroundColor: '#06b6d4',
                },
                {
                    label: '% Source Supply',
                    data: supplyData,
                    backgroundColor: 'rgba(245, 158, 11, 0.25)',
                    borderColor: '#f59e0b',
                    borderWidth: 2,
                    pointBackgroundColor: '#f59e0b',
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                r: {
                    angleLines: { color: 'rgba(51, 65, 85, 0.6)' },
                    grid: { color: 'rgba(51, 65, 85, 0.6)' },
                    pointLabels: {
                        color: '#94a3b8',
                        font: { family: 'monospace', size: 10 }
                    },
                    ticks: {
                        backdropColor: 'transparent',
                        color: '#64748b',
                        font: { size: 9 },
                        showLabelBackdrop: false
                    },
                    suggestedMin: 0,
                    suggestedMax: 40
                }
            },
            plugins: {
                legend: {
                    display: false
                }
            }
        }
    });
}

// Render Pip Demand vs Source Supply Color Cards
function renderPipSourceCards(pipBreakdown) {
    const container = document.getElementById('colorPipCardsGrid');
    if (!container) return;

    const colorConfig = {
        'W': { name: 'White', bg: '#FFFBD5', text: 'text-amber-100' },
        'U': { name: 'Blue', bg: '#0E68AB', text: 'text-blue-400' },
        'B': { name: 'Black', bg: '#150B00', text: 'text-slate-300' },
        'R': { name: 'Red', bg: '#D3202A', text: 'text-rose-400' },
        'G': { name: 'Green', bg: '#00733E', text: 'text-emerald-400' },
        'C': { name: 'Colorless', bg: '#94A3B8', text: 'text-slate-300' },
    };

    const keys = ['W', 'U', 'B', 'R', 'G', 'C'];
    container.innerHTML = keys.map(k => {
        const item = pipBreakdown[k] || { pips: 0, sources: 0, pip_demand_pct: 0, source_supply_pct: 0, balance_ratio: 1.0, status: 'none' };
        const cfg = colorConfig[k];
        
        let statusBadge = '<span class="text-[9px] px-1.5 py-0.2 rounded-none bg-slate-800 text-slate-500 font-mono">Inactive</span>';
        if (item.status === 'balanced') {
            statusBadge = '<span class="text-[9px] px-1.5 py-0.2 rounded-none bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 font-mono font-bold">Balanced</span>';
        } else if (item.status === 'surplus') {
            statusBadge = '<span class="text-[9px] px-1.5 py-0.2 rounded-none bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 font-mono font-bold">Surplus</span>';
        } else if (item.status === 'deficit') {
            statusBadge = '<span class="text-[9px] px-1.5 py-0.2 rounded-none bg-rose-500/20 text-rose-400 border border-rose-500/30 font-mono font-bold">Deficit</span>';
        }

        return `
            <div class="bg-[#1B2230]/70 border border-[#263245] rounded-none p-3 space-y-2">
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-1.5">
                        <span class="w-3 h-3 rounded-full border border-[#263245]" style="background-color: ${cfg.bg}"></span>
                        <span class="text-xs font-mono font-bold ${cfg.text}">${cfg.name}</span>
                    </div>
                    ${statusBadge}
                </div>
                <div class="space-y-1 text-[11px] font-mono">
                    <div class="flex justify-between">
                        <span class="text-slate-400">Demand:</span>
                        <span class="text-cyan-400 font-bold">${item.pips} <span class="text-slate-500">(${item.pip_demand_pct}%)</span></span>
                    </div>
                    <div class="flex justify-between">
                        <span class="text-slate-400">Supply:</span>
                        <span class="text-amber-400 font-bold">${item.sources} <span class="text-slate-500">(${item.source_supply_pct}%)</span></span>
                    </div>
                    <div class="flex justify-between border-t border-[#263245] pt-1">
                        <span class="text-slate-400">Ratio:</span>
                        <span class="text-white font-bold">${item.balance_ratio}x</span>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function getCardClassificationBadges(c) {
    const cls = c.classification || {};
    const badges = [];
    if (cls.is_ramp) {
        if (cls.ramp_tier === 'fast' || (c.cmc !== undefined && c.cmc <= 2)) {
            badges.push('<span class="text-[9px] px-1.5 py-0.5 rounded-none bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 font-mono font-bold uppercase">⚡ Fast Ramp</span>');
        } else {
            badges.push('<span class="text-[9px] px-1.5 py-0.5 rounded-none bg-emerald-500/10 text-emerald-300 border border-emerald-500/20 font-mono uppercase">🛡️ Std Ramp</span>');
        }
    }
    if (cls.is_board_wipe) {
        badges.push('<span class="text-[9px] px-1.5 py-0.5 rounded-none bg-rose-500/20 text-rose-400 border border-rose-500/40 font-mono font-bold uppercase">💥 Wipe</span>');
    } else if (cls.is_targeted_removal) {
        badges.push('<span class="text-[9px] px-1.5 py-0.5 rounded-none bg-amber-500/20 text-amber-400 border border-amber-500/40 font-mono font-bold uppercase">🎯 Removal</span>');
    }
    if (cls.is_draw) {
        if (cls.draw_type === 'engine') badges.push('<span class="text-[9px] px-1.5 py-0.5 rounded-none bg-purple-500/20 text-purple-400 border border-purple-500/40 font-mono font-bold uppercase">🔄 Engine</span>');
        else if (cls.draw_type === 'cantrip') badges.push('<span class="text-[9px] px-1.5 py-0.5 rounded-none bg-blue-500/20 text-blue-300 border border-blue-500/30 font-mono uppercase">🃏 Cantrip</span>');
        else badges.push('<span class="text-[9px] px-1.5 py-0.5 rounded-none bg-purple-500/10 text-purple-300 border border-purple-500/20 font-mono uppercase">📦 Burst</span>');
    }
    if (cls.is_tutor) {
        if (cls.tutor_type === 'general') badges.push('<span class="text-[9px] px-1.5 py-0.5 rounded-none bg-cyan-500/20 text-cyan-400 border border-cyan-500/40 font-mono font-bold uppercase">🔍 Tutor</span>');
        else badges.push('<span class="text-[9px] px-1.5 py-0.5 rounded-none bg-teal-500/20 text-teal-300 border border-teal-500/30 font-mono uppercase">🌱 Land Fetch</span>');
    }
    if (cls.is_tapland) {
        badges.push('<span class="text-[9px] px-1.5 py-0.5 rounded-none bg-rose-500/10 text-rose-300 border border-rose-500/20 font-mono uppercase">⏳ Tapland</span>');
    }
    return badges.join(' ');
}

// Render Format Legality & Rules Evaluation Banner
function renderRulesEvaluation(deck) {
    const banner = document.getElementById('rulesEvaluationBanner');
    if (!banner) return;
    if (!deck) {
        banner.classList.add('hidden');
        return;
    }

    const ruleEval = deck.rule_evaluation || (deck.stats && deck.stats.rule_evaluation) || null;
    const isPauper = Boolean(deck.is_pauper || (deck.deck_format && deck.deck_format.toLowerCase().includes('pauper')));
    const formatName = isPauper ? "Pauper Commander (PDH)" : "Commander (EDH)";

    if (!ruleEval) {
        banner.classList.add('hidden');
        return;
    }

    banner.classList.remove('hidden');

    const isLegal = ruleEval.is_legal !== false && (ruleEval.violations_count === 0 || !ruleEval.violations || ruleEval.violations.length === 0);
    const violations = ruleEval.violations || [];
    const warnings = ruleEval.warnings || [];
    const rarityCounts = ruleEval.rarity_counts || {};

    let html = '';
    if (isLegal) {
        html = `
            <div class="bg-emerald-950/25 border border-emerald-500/30 rounded-none p-4 shadow-lg flex flex-col md:flex-row md:items-center justify-between gap-3">
                <div class="flex items-start sm:items-center gap-3">
                    <div class="w-8 h-8 rounded-none bg-emerald-500/20 border border-emerald-500/40 text-emerald-400 flex items-center justify-center font-bold text-sm flex-shrink-0">
                        ✓
                    </div>
                    <div>
                        <div class="flex items-center gap-2 flex-wrap">
                            <span class="text-xs font-mono font-bold text-white uppercase tracking-wider">${escapeHtml(formatName)} Legal</span>
                            <span class="text-[10px] px-1.5 py-0.2 rounded-none bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-mono">100% Rules Compliant</span>
                        </div>
                        <p class="text-[11px] text-slate-400 font-mono mt-0.5">
                            ${isPauper 
                                ? "All 99 library cards are printed at Common rarity, commander is an eligible Uncommon creature, and no banned cards are present." 
                                : "Deck satisfies official singleton, color identity, and banlist parameters."}
                        </p>
                    </div>
                </div>
                <div class="flex items-center gap-2 flex-wrap md:justify-end">
                    <div class="flex items-center gap-1.5 font-mono text-[10px] text-slate-400 bg-slate-900/80 px-2.5 py-1 rounded-none border border-[#263245]">
                        <span>C: <strong class="text-emerald-400">${rarityCounts.common || 0}</strong></span>
                        <span class="text-slate-600">|</span>
                        <span>U: <strong class="text-cyan-400">${rarityCounts.uncommon || 0}</strong></span>
                        <span class="text-slate-600">|</span>
                        <span>R: <strong class="text-amber-400">${rarityCounts.rare || 0}</strong></span>
                        <span class="text-slate-600">|</span>
                        <span>M: <strong class="text-rose-400">${rarityCounts.mythic || 0}</strong></span>
                    </div>
                    <button type="button" onclick="toggleActiveDeckFormat()" class="text-[10px] font-mono px-2.5 py-1 rounded-none bg-slate-800 hover:bg-slate-700 text-slate-300 border border-[#263245] hover:border-cyan-400 transition cursor-pointer flex items-center gap-1">
                        <span>Format: ${isPauper ? 'PDH' : 'EDH'}</span>
                        <span class="text-[9px] text-cyan-400">⇄ Switch</span>
                    </button>
                </div>
            </div>
        `;
    } else {
        html = `
            <div class="bg-rose-950/20 border border-rose-500/40 rounded-none p-4 shadow-lg space-y-3">
                <div class="flex flex-col md:flex-row md:items-center justify-between gap-3 border-b border-rose-500/20 pb-3">
                    <div class="flex items-start sm:items-center gap-3">
                        <div class="w-8 h-8 rounded-none bg-rose-500/20 border border-rose-500/40 text-rose-400 flex items-center justify-center font-bold text-sm flex-shrink-0">
                            ⚠️
                        </div>
                        <div>
                            <div class="flex items-center gap-2 flex-wrap">
                                <span class="text-xs font-mono font-bold text-rose-300 uppercase tracking-wider">${escapeHtml(formatName)} Violations Detected</span>
                                <span class="text-[10px] px-1.5 py-0.2 rounded-none bg-rose-500/20 text-rose-300 border border-rose-500/40 font-mono font-bold">${violations.length} Illegal ${violations.length === 1 ? 'Card' : 'Cards'}</span>
                            </div>
                            <p class="text-[11px] text-slate-400 font-mono mt-0.5">
                                ${isPauper 
                                    ? "Pauper Commander requires an Uncommon creature commander and all 99 deck cards to have a Common printing." 
                                    : "Deck does not meet standard Commander format criteria."}
                            </p>
                        </div>
                    </div>
                    <div class="flex items-center gap-2 flex-wrap md:justify-end">
                        <div class="flex items-center gap-1.5 font-mono text-[10px] text-slate-400 bg-slate-900/80 px-2.5 py-1 rounded-none border border-[#263245]">
                            <span>C: <strong class="text-slate-200">${rarityCounts.common || 0}</strong></span>
                            <span class="text-slate-600">|</span>
                            <span>U: <strong class="${isPauper ? 'text-amber-400' : 'text-slate-200'}">${rarityCounts.uncommon || 0}</strong></span>
                            <span class="text-slate-600">|</span>
                            <span>R: <strong class="${isPauper ? 'text-rose-400' : 'text-slate-200'}">${rarityCounts.rare || 0}</strong></span>
                            <span class="text-slate-600">|</span>
                            <span>M: <strong class="${isPauper ? 'text-rose-400' : 'text-slate-200'}">${rarityCounts.mythic || 0}</strong></span>
                        </div>
                        <button type="button" onclick="toggleActiveDeckFormat()" class="text-[10px] font-mono px-2.5 py-1 rounded-none bg-slate-800 hover:bg-slate-700 text-cyan-300 border border-[#263245] hover:border-cyan-400 transition cursor-pointer flex items-center gap-1">
                            <span>${isPauper ? 'Switch to Regular EDH' : 'Switch to PDH'}</span>
                            <span class="text-[9px] text-cyan-400">⇄</span>
                        </button>
                    </div>
                </div>
                
                <!-- Violations List -->
                <div class="space-y-1.5 max-h-48 overflow-y-auto pr-1 font-mono text-xs">
                    ${violations.map(v => `
                        <div class="flex items-center justify-between gap-2 p-2 rounded-none bg-[#10141D]/90 border border-rose-500/20 text-slate-300">
                            <div class="flex items-center gap-2 min-w-0">
                                <span class="text-rose-400 font-bold flex-shrink-0">&times;</span>
                                <span class="font-bold text-rose-200 truncate">${escapeHtml(v.card || 'Card')}</span>
                                <span class="text-slate-400 text-[11px] truncate hidden sm:inline">&mdash; ${escapeHtml(v.message || '')}</span>
                            </div>
                            <span class="text-[9px] px-1.5 py-0.5 rounded-none bg-rose-950/60 border border-rose-500/30 text-rose-300 uppercase flex-shrink-0">${escapeHtml(v.type || 'error')}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }

    banner.innerHTML = html;
}

// Toggle active deck format between Commander and Pauper Commander
async function toggleActiveDeckFormat() {
    if (!activeDeck) return;
    const currentIsPauper = Boolean(activeDeck.is_pauper || (activeDeck.deck_format && activeDeck.deck_format.toLowerCase().includes('pauper')));
    const newIsPauper = !currentIsPauper;
    const newFormat = newIsPauper ? 'pauper_commander' : 'commander';

    if (!activeDeck.id) {
        activeDeck.is_pauper = newIsPauper;
        activeDeck.deck_format = newFormat;
        displayDeckInInspector(activeDeck, true);
        showToast(`Switched format to ${newIsPauper ? 'Pauper Commander (PDH)' : 'Regular Commander (EDH)'}`, "info");
        return;
    }

    const badge = document.getElementById('inspectorFormatBadge');
    if (badge) {
        badge.classList.add('opacity-50', 'pointer-events-none');
    }

    try {
        const resp = await fetch(`/api/deck/${activeDeck.id}/format`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_pauper: newIsPauper, deck_format: newFormat })
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.error || "Failed to update deck format");
        }

        activeDeck = data.deck;
        if (data.rule_evaluation) {
            activeDeck.rule_evaluation = data.rule_evaluation;
            if (!activeDeck.stats) activeDeck.stats = {};
            activeDeck.stats.rule_evaluation = data.rule_evaluation;
        }

        showToast(`Deck format updated to ${newIsPauper ? 'Pauper Commander (PDH)' : 'Regular Commander (EDH)'}!`, "success");
        displayDeckInInspector(activeDeck, true);
        await refreshVaultList();

        // If upgrades tab is active or loaded, reload to apply Pauper/Commander filters
        if (currentInspectorTab === 'upgrades') {
            loadDualTierUpgrades(activeDeck.id);
        }
    } catch (e) {
        showToast(`Format toggle error: ${e.message}`, "error");
    } finally {
        if (badge) {
            badge.classList.remove('opacity-50', 'pointer-events-none');
        }
    }
}

// Render Mana Curve Bar Chart with Click Drilldown
function renderManaCurve(curve) {
    const container = document.getElementById('manaCurveContainer');
    if (!container) return;
    const maxVal = Math.max(1, ...Object.values(curve));
    const keys = ["0", "1", "2", "3", "4", "5", "6", "7+"];

    container.innerHTML = keys.map(k => {
        const count = curve[k] || 0;
        const heightPct = Math.max(8, Math.round((count / maxVal) * 100));
        return `
            <div class="flex-1 flex flex-col items-center h-full justify-end group cursor-pointer"
                 title="Filter cards by CMC ${k}"
                 onclick="drillDownToCardRegistry('cmc', '${k}', 'CMC ${k}')">
                <span class="text-[10px] font-mono text-cyan-400 font-bold mb-1 opacity-0 group-hover:opacity-100 transition">${count}</span>
                <div class="w-full bg-gradient-to-t from-cyan-600 to-cyan-400 group-hover:from-cyan-400 group-hover:to-cyan-200 rounded-t transition-all duration-300 shadow-lg shadow-cyan-500/10"
                     style="height: ${heightPct}%;"></div>
            </div>
        `;
    }).join('');
}

// Render Card Type Breakdown List with Click Drilldown
function renderTypeBreakdown(types, totalCards) {
    const container = document.getElementById('typeBreakdownList');
    if (!container) return;
    const total = totalCards || 100;

    container.innerHTML = Object.entries(types).map(([typeName, count]) => {
        const pct = Math.round((count / total) * 100);
        return `
            <div class="cursor-pointer hover:bg-slate-800/40 p-1 rounded-none transition"
                 title="Filter cards by type ${escapeHtml(typeName)}"
                 onclick="drillDownToCardRegistry('type', '${escapeHtml(typeName.toLowerCase())}', 'Type: ${escapeHtml(typeName)}')">
                <div class="flex justify-between text-xs font-mono mb-1">
                    <span class="text-slate-300">${escapeHtml(typeName)}</span>
                    <span class="text-cyan-400 font-bold">${count} <span class="text-slate-500 font-normal">(${pct}%)</span></span>
                </div>
                <div class="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden">
                    <div class="h-full bg-cyan-500 rounded-full" style="width: ${pct}%;"></div>
                </div>
            </div>
        `;
    }).join('');
}

// Build normalized lookup map from AI card ratings
function getCardRatingsMap() {
    const map = new Map();
    if (!activeDeck || !activeDeck.analysis || !Array.isArray(activeDeck.analysis.card_ratings)) {
        return map;
    }
    for (const r of activeDeck.analysis.card_ratings) {
        if (!r || !r.card_name) continue;
        const norm = stripDiacritics(r.card_name).trim();
        map.set(norm, r);
        if (norm.includes(' // ')) {
            map.set(norm.split(' // ')[0].trim(), r);
        }
    }
    return map;
}

// Get AI rating record for a card
function getRatingForCard(c, ratingsMap) {
    if (!c || !c.name) return null;
    const norm = stripDiacritics(c.name).trim();
    if (ratingsMap.has(norm)) return ratingsMap.get(norm);
    if (norm.includes(' // ')) {
        const front = norm.split(' // ')[0].trim();
        if (ratingsMap.has(front)) return ratingsMap.get(front);
    }
    return null;
}

// Format tier styling for AI rating scores
function getRatingTierMeta(score) {
    if (score === null || score === undefined || isNaN(score)) {
        return {
            badgeClass: "bg-slate-800/90 text-slate-400 border border-[#263245]",
            tierLabel: "Unrated",
            tierTextClass: "text-slate-400",
            borderClass: "border-[#263245]"
        };
    }
    if (score >= 9.0) {
        return {
            badgeClass: "bg-cyan-500/15 text-cyan-300 border border-cyan-500/40 shadow-sm",
            tierLabel: "Core Staple",
            tierTextClass: "text-cyan-400 font-bold",
            borderClass: "border-cyan-400"
        };
    }
    if (score >= 7.0) {
        return {
            badgeClass: "bg-blue-500/15 text-blue-300 border border-blue-500/40 shadow-sm",
            tierLabel: "Synergizer",
            tierTextClass: "text-blue-400 font-bold",
            borderClass: "border-blue-400"
        };
    }
    if (score >= 5.0) {
        return {
            badgeClass: "bg-amber-500/15 text-amber-300 border border-amber-500/40 shadow-sm",
            tierLabel: "Role Player",
            tierTextClass: "text-amber-400 font-bold",
            borderClass: "border-amber-400"
        };
    }
    return {
        badgeClass: "bg-rose-500/15 text-rose-300 border border-rose-500/40 shadow-sm",
        tierLabel: "Cut Candidate",
        tierTextClass: "text-rose-400 font-bold",
        borderClass: "border-rose-400"
    };
}

// Render Full Card Registry (Desktop Table & Mobile Tiles)
function renderCardRegistryTable(cards) {
    if (cards) {
        currentDeckCards = cards;
    }
    sortAndFilterCardRegistry();
}

function sortAndFilterCardRegistry() {
    const cards = currentDeckCards || [];
    const searchInput = document.getElementById('cardTableSearchInput');
    const query = stripDiacritics(searchInput ? searchInput.value : '').trim();
    const btnClearSearch = document.getElementById('btnClearCardSearch');
    if (btnClearSearch) {
        if (query) btnClearSearch.classList.remove('hidden');
        else btnClearSearch.classList.add('hidden');
    }

    const ratingsMap = getCardRatingsMap();
    const hasAi = Boolean(activeDeck && activeDeck.has_ai_analysis && ratingsMap.size > 0);

    // Filter cards
    const filtered = cards.filter(c => {
        const name = stripDiacritics(c.name || '');
        const type = stripDiacritics(c.type_line || '');
        const oracle = stripDiacritics(c.oracle_text || '');
        const r = getRatingForCard(c, ratingsMap);
        const role = r ? stripDiacritics(r.role || '') : '';
        const purpose = r ? stripDiacritics(r.purpose || '') : '';

        // Search text check
        const matchesQuery = !query || name.includes(query) || type.includes(query) || oracle.includes(query) || role.includes(query) || purpose.includes(query);
        if (!matchesQuery) return false;

        // Type filter check
        if (currentCardTypeFilter !== 'all') {
            if (!type.includes(currentCardTypeFilter)) return false;
        }

        // Rating tier filter check
        if (currentCardRatingFilter !== 'all') {
            const score = r ? (parseFloat(r.rating) || 7.0) : null;
            if (currentCardRatingFilter === 'unrated') {
                if (score !== null) return false;
            } else if (currentCardRatingFilter === '9') {
                if (score === null || score < 9.0) return false;
            } else if (currentCardRatingFilter === '7') {
                if (score === null || score < 7.0 || score >= 9.0) return false;
            } else if (currentCardRatingFilter === '5') {
                if (score === null || score < 5.0 || score >= 7.0) return false;
            } else if (currentCardRatingFilter === 'cuts') {
                if (score === null || score >= 5.0) return false;
            }
        }

        // Ownership & Multi-Deck Allocation filter check
        if (currentCardOwnershipFilter !== 'all') {
            const isOwned = Boolean(c.is_owned || (c.owned_qty !== undefined && c.owned_qty > 0));
            const hasOtherDecks = Boolean(c.other_deck_count > 0 || (c.other_decks && c.other_decks.length > 0));
            const hasConflict = Boolean(c.copy_deficit && c.copy_deficit > 0);

            if (currentCardOwnershipFilter === 'owned' && !isOwned) return false;
            if (currentCardOwnershipFilter === 'unowned' && isOwned) return false;
            if (currentCardOwnershipFilter === 'shared' && !hasOtherDecks) return false;
            if (currentCardOwnershipFilter === 'conflict' && !hasConflict) return false;
        }

        // Active Telemetry Drilldown check
        if (currentCardDrilldownFilter) {
            const { type: drillType, value: drillVal } = currentCardDrilldownFilter;
            const isLand = type.includes('land');
            if (drillType === 'cmc') {
                if (drillVal === '7+') {
                    if (isLand || (c.cmc === undefined || c.cmc < 7)) return false;
                } else {
                    const cmcTarget = parseInt(drillVal, 10);
                    if (isLand || Math.floor(c.cmc || 0) !== cmcTarget) return false;
                }
            } else if (drillType === 'type') {
                if (!type.includes(drillVal.toLowerCase())) return false;
            } else if (drillType === 'role') {
                const cls = c.classification || {};
                if (drillVal === 'fast_ramp') {
                    if (!(cls.is_ramp && (cls.ramp_tier === 'fast' || c.cmc <= 2))) return false;
                } else if (drillVal === 'std_ramp') {
                    if (!(cls.is_ramp && !(cls.ramp_tier === 'fast' || c.cmc <= 2))) return false;
                } else if (drillVal === 'targeted_removal') {
                    if (!cls.is_targeted_removal) return false;
                } else if (drillVal === 'board_wipe') {
                    if (!cls.is_board_wipe) return false;
                } else if (drillVal === 'engine_draw') {
                    if (!(cls.is_draw && cls.draw_type === 'engine')) return false;
                } else if (drillVal === 'cantrip') {
                    if (!(cls.is_draw && cls.draw_type !== 'engine')) return false;
                } else if (drillVal === 'tutor') {
                    if (!cls.is_tutor) return false;
                }
            }
        }

        return true;
    });

    currentFilteredCards = filtered;

    // Update Counter & Reset Button
    const countEl = document.getElementById('cardRegistryFilteredCount');
    const totalEl = document.getElementById('cardRegistryTotalCount');
    const resetBtn = document.getElementById('btnResetCardFilters');
    if (countEl) countEl.innerText = filtered.length;
    if (totalEl) totalEl.innerText = cards.length;
    if (resetBtn) {
        const isFiltered = Boolean(query || currentCardTypeFilter !== 'all' || currentCardRatingFilter !== 'all' || currentCardOwnershipFilter !== 'all' || currentCardDrilldownFilter);
        if (isFiltered) resetBtn.classList.remove('hidden');
        else resetBtn.classList.add('hidden');
    }

    // Sort cards
    filtered.sort((a, b) => {
        const rA = getRatingForCard(a, ratingsMap);
        const rB = getRatingForCard(b, ratingsMap);
        const sA = rA ? (parseFloat(rA.rating) || 7.0) : (hasAi ? -1 : 0);
        const sB = rB ? (parseFloat(rB.rating) || 7.0) : (hasAi ? -1 : 0);
        const cmcA = a.cmc !== undefined ? parseFloat(a.cmc) : 0;
        const cmcB = b.cmc !== undefined ? parseFloat(b.cmc) : 0;
        const pA = a.price_usd ? parseFloat(a.price_usd) : 0;
        const pB = b.price_usd ? parseFloat(b.price_usd) : 0;
        const nA = (a.name || '').toLowerCase();
        const nB = (b.name || '').toLowerCase();
        const tA = (a.type_line || '').toLowerCase();
        const tB = (b.type_line || '').toLowerCase();

        switch (currentCardSortKey) {
            case 'rating_desc':
                if (sB !== sA) return sB - sA;
                return cmcA - cmcB;
            case 'rating_asc':
                if (sA >= 0 && sB >= 0 && sA !== sB) return sA - sB;
                if (sA >= 0 && sB < 0) return -1;
                if (sB >= 0 && sA < 0) return 1;
                return cmcA - cmcB;
            case 'cmc_asc':
                if (cmcA !== cmcB) return cmcA - cmcB;
                return nA.localeCompare(nB);
            case 'cmc_desc':
                if (cmcB !== cmcA) return cmcB - cmcA;
                return nA.localeCompare(nB);
            case 'price_desc':
                if (pB !== pA) return pB - pA;
                return nA.localeCompare(nB);
            case 'price_asc':
                if (pA !== pB) return pA - pB;
                return nA.localeCompare(nB);
            case 'name_asc':
                return nA.localeCompare(nB);
            case 'name_desc':
                return nB.localeCompare(nA);
            case 'type_asc':
                if (tA !== tB) return tA.localeCompare(tB);
                return cmcA - cmcB;
            default:
                return 0;
        }
    });

    // Update column sort indicators
    updateTableSortIndicators();

    // Render Stacks (Archidekt / Moxfield cascading columns)
    renderCardStacks(filtered, ratingsMap);

    // Render Table Rows (Desktop)
    renderDesktopTable(filtered, ratingsMap);

    // Render Mobile Tiles
    renderMobileTiles(filtered, ratingsMap);

    // Update Ownership Summary Bar
    if (activeDeck) {
        renderOwnershipSummaryStrip(activeDeck);
    }

    // Ensure correct view mode is displayed
    applyCardViewMode();
}

function renderDesktopTable(cards, ratingsMap) {
    const tbody = document.getElementById('cardRegistryTableBody');
    if (!tbody) return;

    if (!cards || cards.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="py-8 text-center text-slate-500 font-mono">No matching cards found. Try adjusting filters or search.</td></tr>`;
        return;
    }

    tbody.innerHTML = cards.map((c, idx) => {
        const price = c.price_usd ? `$${parseFloat(c.price_usd).toFixed(2)}` : '--';
        const img = c.image_uri || c.small_image_uri || '';
        const setCode = c.set_code || '';
        const name = c.name || '';
        const roleBadges = getCardClassificationBadges(c);
        const r = getRatingForCard(c, ratingsMap);
        const score = r ? (parseFloat(r.rating) || 7.0) : null;
        const tier = getRatingTierMeta(score);

        // Ownership and Multi-Deck Allocation Badges
        const isOwned = c.is_owned === true || (c.owned_qty !== undefined && c.owned_qty > 0);
        const ownedQty = c.owned_qty || 0;
        const copyDeficit = c.copy_deficit || 0;
        const otherDecks = c.other_decks || [];

        let ownBadge = '';
        if (isOwned) {
            ownBadge = `<span class="px-1.5 py-0.2 rounded-none text-[9px] font-mono font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 whitespace-nowrap" title="Owned in Collection: ${ownedQty} physical copies">🎴 Owned (${ownedQty})</span>`;
        } else {
            ownBadge = `<span class="px-1.5 py-0.2 rounded-none text-[9px] font-mono font-bold bg-slate-800 text-slate-400 border border-[#263245] whitespace-nowrap" title="Not logged in user collection">🛒 Unowned</span>`;
        }

        let sharedBadge = '';
        if (otherDecks.length > 0) {
            if (copyDeficit > 0) {
                sharedBadge = `<span class="px-1.5 py-0.2 rounded-none text-[9px] font-mono font-bold bg-amber-500/15 text-amber-300 border border-amber-500/40 cursor-help whitespace-nowrap" title="Slotted in ${otherDecks.length + 1} decks (${otherDecks.join(', ')}). Copy Deficit: need ${copyDeficit} more copy!">⚠️ Shared (${otherDecks.length + 1} decks, -${copyDeficit})</span>`;
            } else {
                sharedBadge = `<span class="px-1.5 py-0.2 rounded-none text-[9px] font-mono font-bold bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 cursor-help whitespace-nowrap" title="Also slotted in: ${otherDecks.join(', ')}">🔗 Shared (${otherDecks.length + 1} decks)</span>`;
            }
        }

        let ratingCellHtml = '';
        if (score !== null) {
            ratingCellHtml = `
                <div class="flex flex-col gap-1 min-w-[130px] max-w-[240px]">
                    <div class="flex items-center gap-1.5">
                        <span class="px-1.5 py-0.5 rounded-none text-[11px] font-mono font-black ${tier.badgeClass}">
                            ★ ${score.toFixed(1)}
                        </span>
                        ${r.role ? `<span class="px-1.5 py-0.5 rounded-none text-[9px] font-mono font-bold uppercase bg-slate-800 text-slate-300 border border-[#263245] truncate max-w-[110px]" title="${escapeHtml(r.role)}">${escapeHtml(r.role)}</span>` : ''}
                    </div>
                    ${r.purpose ? `
                        <p class="text-[11px] text-slate-400 font-sans italic truncate hover:whitespace-normal transition cursor-help leading-snug" title="${escapeHtml(r.purpose)}">
                            "${escapeHtml(r.purpose)}"
                        </p>
                    ` : ''}
                </div>
            `;
        } else {
            ratingCellHtml = `<span class="text-slate-600 font-mono text-xs" title="Not rated yet - rerun AI Intel to evaluate">--</span>`;
        }

        return `
            <tr class="hover:bg-[#1B2230]/50 transition group card-row" data-name="${escapeHtml(name.toLowerCase())}" data-type="${escapeHtml((c.type_line || '').toLowerCase())}">
                <td class="py-2.5 px-3 text-center font-bold text-cyan-400">${c.quantity || 1}x</td>
                <td class="py-2.5 px-3 font-semibold text-white">
                    <div class="flex flex-col gap-0.5">
                        <span class="cursor-pointer hover:text-cyan-300 hover:underline inline-flex items-center gap-1.5"
                              data-img="${escapeHtml(img)}"
                              data-card-name="${escapeHtml(name)}"
                              onclick="showCardHover(event, this)"
                              onmouseenter="showCardHover(event, this)" onmousemove="moveCardHover(event)" onmouseleave="hideCardHover()">
                            ${escapeHtml(name)}
                            ${c.section === 'commander' ? '<span class="text-[9px] px-1.5 py-0.2 rounded-none bg-cyan-500/20 text-cyan-400 border border-cyan-500/40 font-mono font-bold uppercase">Commander</span>' : ''}
                        </span>
                        <div class="flex flex-wrap items-center gap-1 mt-0.5">
                            ${ownBadge}
                            ${sharedBadge}
                            ${roleBadges ? roleBadges : ''}
                        </div>
                    </div>
                </td>
                <td class="py-2.5 px-3 text-slate-400 text-[11px] truncate max-w-[170px]">${escapeHtml(c.type_line || 'Card')}</td>
                <td class="py-2.5 px-3 text-slate-300 font-sans text-xs whitespace-nowrap">${escapeHtml(c.mana_cost || '')}</td>
                <td class="py-2.5 px-3 text-center text-amber-400 font-bold">${c.cmc !== undefined ? c.cmc : 0}</td>
                <td class="py-2.5 px-3">${ratingCellHtml}</td>
                <td class="py-2.5 px-3 text-right font-bold text-emerald-400 whitespace-nowrap">${price}</td>
                <td class="py-2.5 px-3 text-center">
                    <div class="flex items-center justify-center gap-1.5">
                        <button onclick="handleWatchClick(this)" 
                                data-name="${escapeHtml(name)}" 
                                data-set="${escapeHtml(setCode)}" 
                                title="Track in Watchlist"
                                class="watch-card-btn px-2 py-1 bg-[#1B2230] hover:bg-cyan-500 hover:text-slate-950 text-cyan-400 rounded-none text-[10px] font-mono uppercase font-bold border border-[#263245] transition flex items-center gap-1 cursor-pointer">
                            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
                            <span>+ Watch</span>
                        </button>
                        ${c.section !== 'commander' ? `
                        <button onclick="handleCutCardClick(this)"
                                data-card-name="${escapeHtml(name)}"
                                title="Cut / Remove card from deck"
                                class="cut-card-btn px-2 py-1 bg-rose-500/10 hover:bg-rose-500 hover:text-white text-rose-400 rounded-none text-[10px] font-mono uppercase font-bold border border-rose-500/30 transition flex items-center gap-1 cursor-pointer">
                            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                            <span>Cut</span>
                        </button>` : ''}
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function renderMobileTiles(cards, ratingsMap) {
    const list = document.getElementById('mobileCardRegistryList');
    if (!list) return;

    if (!cards || cards.length === 0) {
        list.innerHTML = `<div class="p-8 text-center bg-[#10141D] border border-[#263245] rounded-none text-slate-500 font-mono text-xs">No matching cards found. Try adjusting filters or search.</div>`;
        return;
    }

    list.innerHTML = cards.map((c, idx) => {
        const price = c.price_usd ? `$${parseFloat(c.price_usd).toFixed(2)}` : '--';
        const img = c.image_uri || c.small_image_uri || '';
        const setCode = c.set_code || '';
        const name = c.name || '';
        const roleBadges = getCardClassificationBadges(c);
        const r = getRatingForCard(c, ratingsMap);
        const score = r ? (parseFloat(r.rating) || 7.0) : null;
        const tier = getRatingTierMeta(score);

        // Ownership & Shared
        const isOwned = c.is_owned === true || (c.owned_qty !== undefined && c.owned_qty > 0);
        const ownedQty = c.owned_qty || 0;
        const copyDeficit = c.copy_deficit || 0;
        const otherDecks = c.other_decks || [];

        let ownBadge = '';
        if (isOwned) {
            ownBadge = `<span class="px-1.5 py-0.2 rounded-none text-[9px] font-mono font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 whitespace-nowrap">🎴 Owned (${ownedQty})</span>`;
        } else {
            ownBadge = `<span class="px-1.5 py-0.2 rounded-none text-[9px] font-mono font-bold bg-slate-800 text-slate-400 border border-[#263245] whitespace-nowrap">🛒 Unowned</span>`;
        }

        let sharedBadge = '';
        if (otherDecks.length > 0) {
            if (copyDeficit > 0) {
                sharedBadge = `<span class="px-1.5 py-0.2 rounded-none text-[9px] font-mono font-bold bg-amber-500/15 text-amber-300 border border-amber-500/40 whitespace-nowrap">⚠️ Shared (${otherDecks.length + 1} decks, -${copyDeficit})</span>`;
            } else {
                sharedBadge = `<span class="px-1.5 py-0.2 rounded-none text-[9px] font-mono font-bold bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 whitespace-nowrap">🔗 Shared (${otherDecks.length + 1} decks)</span>`;
            }
        }

        let ratingHtml = '';
        if (score !== null) {
            ratingHtml = `
                <div class="flex items-center gap-1 flex-shrink-0">
                    <span class="px-2 py-0.5 rounded-none text-xs font-mono font-black ${tier.badgeClass}">
                        ★ ${score.toFixed(1)}
                    </span>
                </div>
            `;
        } else {
            ratingHtml = `<span class="text-[10px] font-mono text-slate-500 px-1.5 py-0.5 rounded-none bg-slate-800/80 border border-[#263245]">Unrated</span>`;
        }

        let rationaleHtml = '';
        if (r && r.purpose) {
            rationaleHtml = `
                <div class="bg-[#1B2230]/70 border-l-2 ${tier.borderClass} rounded-r-lg p-2 text-xs space-y-1">
                    <div class="flex items-center justify-between text-[10px] font-mono uppercase">
                        <span class="${tier.tierTextClass}">${tier.tierLabel}</span>
                        ${r.role ? `<span class="text-cyan-400 font-bold bg-cyan-500/10 px-1.5 py-0.2 rounded-none border border-cyan-500/20">${escapeHtml(r.role)}</span>` : ''}
                    </div>
                    <p class="text-slate-300 font-sans text-xs italic leading-relaxed">
                        "${escapeHtml(r.purpose)}"
                    </p>
                </div>
            `;
        }

        return `
            <div class="bg-[#10141D] border border-[#263245] hover:border-[#263245] rounded-none p-3.5 space-y-2.5 shadow-md transition">
                <!-- Top Row: Qty, Name, Mana Cost, Rating Badge -->
                <div class="flex items-start justify-between gap-2">
                    <div class="flex items-start gap-2 min-w-0 flex-1">
                        <span class="px-1.5 py-0.5 rounded-none bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 text-xs font-mono font-bold flex-shrink-0 mt-0.5">
                            ${c.quantity || 1}x
                        </span>
                        <div class="min-w-0 flex-1">
                            <span class="cursor-pointer font-bold text-sm text-white hover:text-cyan-300 leading-snug break-words block"
                                  data-img="${escapeHtml(img)}"
                                  data-card-name="${escapeHtml(name)}"
                                  onclick="showCardHover(event, this)">
                                ${escapeHtml(name)}
                            </span>
                            ${c.section === 'commander' ? '<span class="text-[9px] px-1.5 py-0.2 rounded-none bg-cyan-500/20 text-cyan-400 border border-cyan-500/40 font-mono font-bold uppercase inline-block mt-0.5">Commander</span>' : ''}
                        </div>
                    </div>

                    <div class="flex flex-col items-end gap-1 flex-shrink-0">
                        ${ratingHtml}
                        ${c.mana_cost ? `<span class="text-xs text-slate-300 font-sans">${escapeHtml(c.mana_cost)}</span>` : ''}
                    </div>
                </div>

                <!-- Middle Row: Type Line, CMC, Price -->
                <div class="flex flex-wrap items-center justify-between gap-2 text-xs font-mono border-t border-[#263245] pt-2 text-slate-400">
                    <div class="flex flex-wrap items-center gap-2 min-w-0">
                        <span class="text-slate-300 truncate max-w-[180px]">${escapeHtml(c.type_line || 'Card')}</span>
                        <span class="text-amber-400 font-bold bg-amber-500/10 px-1.5 py-0.2 rounded-none border border-amber-500/20">CMC ${c.cmc !== undefined ? c.cmc : 0}</span>
                    </div>
                    <span class="font-bold text-emerald-400 text-sm">${price}</span>
                </div>

                <!-- AI Tactical Rationale -->
                ${rationaleHtml}

                <!-- Footer: Classification Badges, Ownership Badges & Actions -->
                <div class="flex flex-wrap items-center justify-between gap-2 pt-1.5 border-t border-[#263245]">
                    <div class="flex flex-wrap items-center gap-1 min-w-0">
                        ${ownBadge}
                        ${sharedBadge}
                        ${roleBadges}
                    </div>
                    <div class="flex items-center gap-1.5 flex-shrink-0 ml-auto">
                        <button onclick="handleWatchClick(this)" 
                                data-name="${escapeHtml(name)}" 
                                data-set="${escapeHtml(setCode)}" 
                                title="Track in Watchlist"
                                class="watch-card-btn px-2 py-1 bg-[#1B2230] hover:bg-cyan-500 hover:text-slate-950 text-cyan-400 rounded-none text-[9px] sm:text-[10px] font-mono uppercase font-bold border border-[#263245] transition flex items-center gap-1 cursor-pointer">
                            <svg class="w-3 h-3 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
                            <span>Watch</span>
                        </button>
                        ${c.section !== 'commander' ? `
                        <button onclick="handleCutCardClick(this)"
                                data-card-name="${escapeHtml(name)}"
                                title="Cut / Remove card from deck"
                                class="cut-card-btn px-2 py-1 bg-rose-500/10 hover:bg-rose-500 hover:text-white text-rose-400 rounded-none text-[9px] sm:text-[10px] font-mono uppercase font-bold border border-rose-500/30 transition flex items-center gap-1 cursor-pointer">
                            <svg class="w-3 h-3 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                            <span>Cut</span>
                        </button>` : ''}
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function renderOwnershipSummaryStrip(deck) {
    if (!deck) return;
    const summary = deck.ownership_summary || {};
    const owned = summary.owned_cards || 0;
    const total = summary.total_cards || (deck.cards_data ? deck.cards_data.length : 0);
    const pct = summary.owned_pct !== undefined ? summary.owned_pct : (total > 0 ? Math.round((owned / total) * 100) : 0);
    const cost = summary.unowned_est_cost !== undefined ? `$${summary.unowned_est_cost.toFixed(2)}` : '$0.00';
    const shared = summary.shared_cards || 0;
    const conflicts = summary.deficit_conflicts || 0;

    safeSetText('ownSummaryOwned', `${owned} / ${total} (${pct}%)`);
    safeSetText('ownSummaryCost', cost);
    safeSetText('ownSummaryShared', `${shared} Cards`);

    const conflictBadge = document.getElementById('ownSummaryConflictBadge');
    const conflictCount = document.getElementById('ownSummaryConflictCount');
    if (conflictBadge && conflictCount) {
        if (conflicts > 0) {
            conflictCount.innerText = conflicts;
            conflictBadge.classList.remove('hidden');
        } else {
            conflictBadge.classList.add('hidden');
        }
    }
}

function renderCardStacks(cards, ratingsMap) {
    const container = document.getElementById('cardStacksColumnsContainer');
    if (!container) return;

    if (!cards || cards.length === 0) {
        container.innerHTML = `<div class="p-8 text-center w-full bg-[#10141D] border border-[#263245] text-slate-500 font-mono text-xs">No matching cards found. Try adjusting filters or search.</div>`;
        return;
    }

    // Grouping strategies
    const groups = {};

    if (currentStackGroupBy === 'role') {
        const roleOrder = ['Fast Mana / Ramp', 'Card Draw & Engines', 'Targeted Removal', 'Board Wipes', 'Protection & Counters', 'Tutors', 'Win Conditions', 'Lands', 'Other Support'];
        roleOrder.forEach(r => { groups[r] = []; });

        cards.forEach(c => {
            const cls = c.classification || {};
            const type = (c.type_line || '').toLowerCase();
            if (type.includes('land')) {
                groups['Lands'].push(c);
            } else if (cls.is_ramp) {
                groups['Fast Mana / Ramp'].push(c);
            } else if (cls.is_draw) {
                groups['Card Draw & Engines'].push(c);
            } else if (cls.is_targeted_removal) {
                groups['Targeted Removal'].push(c);
            } else if (cls.is_board_wipe) {
                groups['Board Wipes'].push(c);
            } else if (cls.is_counterspell || cls.is_protection) {
                groups['Protection & Counters'].push(c);
            } else if (cls.is_tutor) {
                groups['Tutors'].push(c);
            } else if (cls.is_wincon) {
                groups['Win Conditions'].push(c);
            } else {
                groups['Other Support'].push(c);
            }
        });
    } else if (currentStackGroupBy === 'type') {
        const typeOrder = ['Creatures', 'Instants', 'Sorceries', 'Artifacts', 'Enchantments', 'Planeswalkers', 'Lands', 'Other'];
        typeOrder.forEach(t => { groups[t] = []; });

        cards.forEach(c => {
            const type = (c.type_line || '').toLowerCase();
            if (type.includes('creature')) groups['Creatures'].push(c);
            else if (type.includes('instant')) groups['Instants'].push(c);
            else if (type.includes('sorcery')) groups['Sorceries'].push(c);
            else if (type.includes('artifact')) groups['Artifacts'].push(c);
            else if (type.includes('enchantment')) groups['Enchantments'].push(c);
            else if (type.includes('planeswalker')) groups['Planeswalkers'].push(c);
            else if (type.includes('land')) groups['Lands'].push(c);
            else groups['Other'].push(c);
        });
    } else {
        // Default: CMC
        const cmcOrder = ['0 CMC', '1 CMC', '2 CMC', '3 CMC', '4 CMC', '5 CMC', '6 CMC', '7+ CMC', 'Lands'];
        cmcOrder.forEach(k => { groups[k] = []; });

        cards.forEach(c => {
            const type = (c.type_line || '').toLowerCase();
            if (type.includes('land')) {
                groups['Lands'].push(c);
            } else {
                const cmc = Math.floor(c.cmc || 0);
                if (cmc >= 7) groups['7+ CMC'].push(c);
                else groups[`${cmc} CMC`].push(c);
            }
        });
    }

    container.innerHTML = Object.entries(groups).map(([groupName, groupCards]) => {
        if (!groupCards || groupCards.length === 0) return '';

        const groupVal = groupCards.reduce((acc, c) => acc + (c.price_usd ? parseFloat(c.price_usd) : 0), 0);

        const cardsHtml = groupCards.map(c => {
            const price = c.price_usd ? `$${parseFloat(c.price_usd).toFixed(2)}` : '--';
            const img = c.image_uri || c.small_image_uri || '';
            const name = c.name || '';
            const r = getRatingForCard(c, ratingsMap);
            const score = r ? (parseFloat(r.rating) || 7.0) : null;
            const tier = getRatingTierMeta(score);

            const isOwned = c.is_owned === true || (c.owned_qty !== undefined && c.owned_qty > 0);
            const ownedQty = c.owned_qty || 0;
            const copyDeficit = c.copy_deficit || 0;
            const otherDecks = c.other_decks || [];

            let ownBadge = isOwned ? 
                `<span class="text-[9px] font-mono font-bold text-emerald-400" title="Owned: ${ownedQty}">🎴 ${ownedQty}</span>` : 
                `<span class="text-[9px] font-mono font-bold text-slate-500" title="Unowned">🛒</span>`;

            let sharedBadge = '';
            if (otherDecks.length > 0) {
                if (copyDeficit > 0) {
                    sharedBadge = `<span class="text-[9px] font-mono font-bold text-amber-400 cursor-help" title="Slotted in ${otherDecks.length + 1} decks (${otherDecks.join(', ')}). Deficit: -${copyDeficit}">⚠️ ${otherDecks.length + 1}</span>`;
                } else {
                    sharedBadge = `<span class="text-[9px] font-mono font-bold text-cyan-400 cursor-help" title="Slotted in ${otherDecks.length + 1} decks (${otherDecks.join(', ')})">🔗 ${otherDecks.length + 1}</span>`;
                }
            }

            return `
                <div class="group/stack bg-[#141A24] hover:bg-[#1B2230] border border-[#263245] hover:border-cyan-500/60 p-2 transition rounded-none shadow-sm flex flex-col gap-1 cursor-pointer"
                     data-img="${escapeHtml(img)}"
                     data-card-name="${escapeHtml(name)}"
                     onclick="showCardHover(event, this)"
                     onmouseenter="showCardHover(event, this)" onmousemove="moveCardHover(event)" onmouseleave="hideCardHover()">
                    <div class="flex items-start justify-between gap-1.5">
                        <div class="flex items-start gap-1.5 min-w-0 flex-1">
                            <span class="text-cyan-400 font-mono font-bold text-xs flex-shrink-0">${c.quantity || 1}x</span>
                            <span class="text-xs font-bold text-white group-hover/stack:text-cyan-300 truncate">${escapeHtml(name)}</span>
                        </div>
                        <span class="text-emerald-400 font-mono font-bold text-xs flex-shrink-0">${price}</span>
                    </div>
                    <div class="flex items-center justify-between text-[10px] text-slate-400 font-mono">
                        <span class="truncate max-w-[130px]">${escapeHtml(c.type_line || '')}</span>
                        ${c.mana_cost ? `<span class="font-sans text-slate-300">${escapeHtml(c.mana_cost)}</span>` : `<span class="text-amber-400">CMC ${c.cmc || 0}</span>`}
                    </div>
                    <div class="flex items-center justify-between pt-1 border-t border-[#263245]/60">
                        <div class="flex items-center gap-1.5">
                            ${ownBadge}
                            ${sharedBadge}
                        </div>
                        ${score !== null ? `<span class="px-1 py-0.2 rounded-none text-[9px] font-mono font-black ${tier.badgeClass}">★ ${score.toFixed(1)}</span>` : ''}
                    </div>
                </div>
            `;
        }).join('');

        return `
            <div class="min-w-[240px] max-w-[280px] flex-1 bg-[#10141D] border border-[#263245] p-3 rounded-none flex flex-col gap-2.5 shadow-md flex-shrink-0">
                <div class="flex items-center justify-between border-b border-[#263245] pb-2">
                    <div class="flex items-center gap-1.5 min-w-0">
                        <span class="w-2 h-2 rounded-full bg-cyan-400 flex-shrink-0"></span>
                        <span class="text-xs font-mono font-bold text-white uppercase truncate">${groupName}</span>
                    </div>
                    <div class="flex items-center gap-2 flex-shrink-0">
                        <span class="text-[10px] font-mono text-emerald-400 font-bold">$${groupVal.toFixed(2)}</span>
                        <span class="text-xs font-mono font-bold text-cyan-400 bg-cyan-500/10 border border-cyan-500/30 px-1.5 py-0.2">${groupCards.length}</span>
                    </div>
                </div>
                <div class="space-y-1.5 max-h-[680px] overflow-y-auto pr-1">
                    ${cardsHtml}
                </div>
            </div>
        `;
    }).join('');
}

function setCardViewMode(mode) {
    currentCardViewMode = mode;
    applyCardViewMode();
}

function applyCardViewMode() {
    const tilesBtn = document.getElementById('cardViewBtnTiles');
    const tableBtn = document.getElementById('cardViewBtnTable');
    const stacksBtn = document.getElementById('cardViewBtnStacks');
    const mobileList = document.getElementById('mobileCardRegistryList');
    const desktopTable = document.getElementById('desktopCardRegistryTableContainer');
    const stacksView = document.getElementById('cardViewStacks');
    const stackGroupByContainer = document.getElementById('stackGroupByContainer');

    const mode = currentCardViewMode;

    if (mode === 'stacks') {
        if (mobileList) mobileList.classList.add('hidden');
        if (desktopTable) desktopTable.classList.add('hidden');
        if (stacksView) stacksView.classList.remove('hidden');
        if (stackGroupByContainer) {
            stackGroupByContainer.classList.remove('hidden');
            stackGroupByContainer.classList.add('flex');
        }
        if (tilesBtn) tilesBtn.className = "px-2.5 py-1 rounded-none text-slate-400 hover:text-white transition flex items-center gap-1";
        if (tableBtn) tableBtn.className = "px-2.5 py-1 rounded-none text-slate-400 hover:text-white transition flex items-center gap-1";
        if (stacksBtn) stacksBtn.className = "px-2.5 py-1 rounded-none text-cyan-400 bg-cyan-500/20 font-bold transition flex items-center gap-1";
    } else if (mode === 'tiles' || (mode === 'auto' && window.innerWidth < 768)) {
        if (mobileList) mobileList.classList.remove('hidden');
        if (desktopTable) desktopTable.classList.add('hidden');
        if (stacksView) stacksView.classList.add('hidden');
        if (stackGroupByContainer) {
            stackGroupByContainer.classList.add('hidden');
            stackGroupByContainer.classList.remove('flex');
        }
        if (tilesBtn) tilesBtn.className = "px-2.5 py-1 rounded-none text-cyan-400 bg-cyan-500/20 font-bold transition flex items-center gap-1";
        if (tableBtn) tableBtn.className = "px-2.5 py-1 rounded-none text-slate-400 hover:text-white transition flex items-center gap-1";
        if (stacksBtn) stacksBtn.className = "px-2.5 py-1 rounded-none text-slate-400 hover:text-white transition flex items-center gap-1";
    } else {
        if (mobileList) mobileList.classList.add('hidden');
        if (desktopTable) desktopTable.classList.remove('hidden');
        if (stacksView) stacksView.classList.add('hidden');
        if (stackGroupByContainer) {
            stackGroupByContainer.classList.add('hidden');
            stackGroupByContainer.classList.remove('flex');
        }
        if (tilesBtn) tilesBtn.className = "px-2.5 py-1 rounded-none text-slate-400 hover:text-white transition flex items-center gap-1";
        if (tableBtn) tableBtn.className = "px-2.5 py-1 rounded-none text-cyan-400 bg-cyan-500/20 font-bold transition flex items-center gap-1";
        if (stacksBtn) stacksBtn.className = "px-2.5 py-1 rounded-none text-slate-400 hover:text-white transition flex items-center gap-1";
    }
}

function setStackGroupBy(groupBy) {
    currentStackGroupBy = groupBy;
    const select = document.getElementById('stackGroupBySelect');
    if (select) select.value = groupBy;
    sortAndFilterCardRegistry();
}

function setCardOwnershipFilter(filter) {
    currentCardOwnershipFilter = filter;
    document.querySelectorAll('.card-ownership-filter-btn').forEach(btn => {
        const f = btn.getAttribute('data-filter');
        if (f === filter) {
            btn.className = "card-ownership-filter-btn px-2 py-0.5 rounded-none bg-cyan-500 text-slate-950 font-bold transition";
        } else {
            let textColor = 'text-slate-400';
            let borderColor = 'border-[#263245]';
            if (f === 'owned') { textColor = 'text-emerald-400'; borderColor = 'border-emerald-500/30'; }
            else if (f === 'unowned') { textColor = 'text-amber-400'; borderColor = 'border-amber-500/30'; }
            else if (f === 'shared') { textColor = 'text-cyan-400'; borderColor = 'border-cyan-500/30'; }
            else if (f === 'conflict') { textColor = 'text-rose-400'; borderColor = 'border-rose-500/30'; }
            btn.className = `card-ownership-filter-btn px-2 py-0.5 rounded-none bg-[#1B2230] ${textColor} hover:text-white border ${borderColor} font-bold transition`;
        }
    });
    sortAndFilterCardRegistry();
}

function drillDownToCardRegistry(type, value, label) {
    currentCardDrilldownFilter = { type, value, label };
    
    // Switch to Cards tab in Inspector
    switchInspectorTab('cards');

    // Update indicator pill in UI
    const container = document.getElementById('cardDrilldownFilterContainer');
    const labelEl = document.getElementById('cardDrilldownFilterLabel');
    if (container) {
        container.classList.remove('hidden');
        container.classList.add('flex');
    }
    if (labelEl) labelEl.innerText = label || `${type}: ${value}`;

    // Filter registry
    sortAndFilterCardRegistry();

    // Smooth scroll down to card registry container if needed
    const registrySection = document.getElementById('cardOwnershipSummaryStrip') || document.getElementById('cardTableSearchInput');
    if (registrySection) {
        registrySection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

function clearCardDrilldownFilter() {
    currentCardDrilldownFilter = null;
    const container = document.getElementById('cardDrilldownFilterContainer');
    if (container) {
        container.classList.add('hidden');
        container.classList.remove('flex');
    }
    sortAndFilterCardRegistry();
}

function setCardSort(sortKey) {
    currentCardSortKey = sortKey;
    const sortSelect = document.getElementById('cardSortSelect');
    if (sortSelect) sortSelect.value = sortKey;
    sortAndFilterCardRegistry();
}

function toggleCardSort(field) {
    let newSort = 'rating_desc';
    if (field === 'rating') {
        newSort = currentCardSortKey === 'rating_desc' ? 'rating_asc' : 'rating_desc';
    } else if (field === 'cmc') {
        newSort = currentCardSortKey === 'cmc_asc' ? 'cmc_desc' : 'cmc_asc';
    } else if (field === 'price') {
        newSort = currentCardSortKey === 'price_desc' ? 'price_asc' : 'price_desc';
    } else if (field === 'name') {
        newSort = currentCardSortKey === 'name_asc' ? 'name_desc' : 'name_asc';
    } else if (field === 'type') {
        newSort = currentCardSortKey === 'type_asc' ? 'name_asc' : 'type_asc';
    }
    setCardSort(newSort);
}

function updateTableSortIndicators() {
    const fields = ['rating', 'cmc', 'price', 'name', 'type'];
    fields.forEach(f => {
        const el = document.getElementById(`sortCol-${f}`);
        if (!el) return;
        if (currentCardSortKey.startsWith(f)) {
            el.innerText = currentCardSortKey.endsWith('desc') ? '▼' : '▲';
            el.className = 'text-[10px] text-cyan-400 font-bold';
        } else {
            el.innerText = '↕';
            el.className = 'text-[10px] text-slate-500';
        }
    });
}

function setCardRatingFilter(val) {
    currentCardRatingFilter = val;
    sortAndFilterCardRegistry();
}

function clearCardSearchInput() {
    const searchInput = document.getElementById('cardTableSearchInput');
    if (searchInput) {
        searchInput.value = '';
    }
    sortAndFilterCardRegistry();
}

function resetCardFilters() {
    const searchInput = document.getElementById('cardTableSearchInput');
    if (searchInput) searchInput.value = '';
    const ratingSelect = document.getElementById('cardRatingFilterSelect');
    if (ratingSelect) ratingSelect.value = 'all';
    currentCardRatingFilter = 'all';
    currentCardTypeFilter = 'all';
    currentCardOwnershipFilter = 'all';
    currentCardDrilldownFilter = null;

    const drilldownContainer = document.getElementById('cardDrilldownFilterContainer');
    if (drilldownContainer) {
        drilldownContainer.classList.add('hidden');
        drilldownContainer.classList.remove('flex');
    }

    document.querySelectorAll('.card-type-filter-btn').forEach(btn => {
        if (btn.getAttribute('data-filter') === 'all') {
            btn.className = "card-type-filter-btn px-2 sm:px-2.5 py-0.5 sm:py-1 text-[10px] sm:text-[11px] font-mono font-bold rounded-none bg-cyan-500 text-slate-950 flex-shrink-0 transition";
        } else {
            btn.className = "card-type-filter-btn px-2 sm:px-2.5 py-0.5 sm:py-1 text-[10px] sm:text-[11px] font-mono font-bold rounded-none bg-[#1B2230] text-slate-400 hover:text-white flex-shrink-0 transition";
        }
    });

    document.querySelectorAll('.card-ownership-filter-btn').forEach(btn => {
        const f = btn.getAttribute('data-filter');
        if (f === 'all') {
            btn.className = "card-ownership-filter-btn px-2 py-0.5 rounded-none bg-cyan-500 text-slate-950 font-bold transition";
        } else {
            let textColor = 'text-slate-400';
            let borderColor = 'border-[#263245]';
            if (f === 'owned') { textColor = 'text-emerald-400'; borderColor = 'border-emerald-500/30'; }
            else if (f === 'unowned') { textColor = 'text-amber-400'; borderColor = 'border-amber-500/30'; }
            else if (f === 'shared') { textColor = 'text-cyan-400'; borderColor = 'border-cyan-500/30'; }
            else if (f === 'conflict') { textColor = 'text-rose-400'; borderColor = 'border-rose-500/30'; }
            btn.className = `card-ownership-filter-btn px-2 py-0.5 rounded-none bg-[#1B2230] ${textColor} hover:text-white border ${borderColor} font-bold transition`;
        }
    });

    sortAndFilterCardRegistry();
}

function renderFleetPositioningChart(decks, metric = 'power') {
    const canvas = document.getElementById('fleetPositioningChart');
    if (!canvas || typeof Chart === 'undefined') return;
    const ctx = canvas.getContext('2d');
    if (fleetPositioningChartInstance) {
        fleetPositioningChartInstance.destroy();
    }

    const availableDecks = (decks && decks.length > 0) ? decks : (allDecks || []);
    if (!availableDecks || availableDecks.length === 0) return;

    const getColorForIdentity = (colorId) => {
        if (!colorId || colorId.length === 0) return 'rgba(148, 163, 184, 0.85)';
        if (colorId.length > 1) return 'rgba(234, 179, 8, 0.85)';
        const c = colorId[0];
        if (c === 'W') return 'rgba(255, 251, 213, 0.9)';
        if (c === 'U') return 'rgba(14, 165, 233, 0.85)';
        if (c === 'B') return 'rgba(168, 85, 247, 0.85)';
        if (c === 'R') return 'rgba(239, 68, 68, 0.85)';
        if (c === 'G') return 'rgba(34, 197, 94, 0.85)';
        return 'rgba(6, 182, 212, 0.85)';
    };

    const dataPoints = availableDecks.map(d => {
        const stats = d.stats || {};
        const analysis = d.analysis || {};
        
        const x = parseFloat(d.avg_cmc || stats.nonland_amv || stats.avg_cmc || 3.2);

        let y = 6.5;
        if (metric === 'power') {
            y = parseFloat(d.power_level !== undefined && d.power_level !== null ? d.power_level : (analysis.estimated_power_level || 7.0)) || 7.0;
        } else if (metric === 'value') {
            y = parseFloat(d.total_value || stats.total_value || 0);
        } else if (metric === 'synergy') {
            y = parseFloat(d.synergy_score || stats.synergy_score || analysis.synergy_score || 75);
        }

        const val = parseFloat(d.total_value || stats.total_value || 100);
        const r = Math.max(7, Math.min(22, Math.round(Math.sqrt(val) * 0.75)));
        const colorId = d.color_identity || stats.color_identity || [];
        const color = getColorForIdentity(colorId);

        return {
            x: Math.round(x * 100) / 100,
            y: Math.round(y * 100) / 100,
            r: r,
            deck: d,
            rawVal: val,
            color: color
        };
    });

    const dataset = {
        label: 'Fleet Decks',
        data: dataPoints,
        backgroundColor: dataPoints.map(p => p.color),
        borderColor: '#0f172a',
        borderWidth: 1.5,
        hoverBorderColor: '#38bdf8',
        hoverBorderWidth: 3
    };

    let yAxisConfig = {
        title: {
            display: true,
            text: metric === 'power' ? 'Estimated Power Level (1 - 10)' : (metric === 'value' ? 'Deck Market Value ($)' : 'Synergy Score (0 - 100)'),
            color: '#94a3b8',
            font: { family: 'monospace', size: 11, weight: 'bold' }
        },
        grid: { color: 'rgba(38, 50, 69, 0.4)' },
        ticks: { color: '#94a3b8', font: { family: 'monospace', size: 10 } }
    };
    if (metric === 'power') {
        yAxisConfig.min = 2;
        yAxisConfig.max = 10;
    } else if (metric === 'synergy') {
        yAxisConfig.min = 40;
        yAxisConfig.max = 100;
    } else {
        yAxisConfig.beginAtZero = true;
    }

    fleetPositioningChartInstance = new Chart(ctx, {
        type: 'bubble',
        data: { datasets: [dataset] },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(15, 23, 42, 0.95)',
                    titleColor: '#38bdf8',
                    bodyColor: '#e2e8f0',
                    borderColor: '#0284c7',
                    borderWidth: 1,
                    padding: 10,
                    titleFont: { family: 'monospace', weight: 'bold', size: 12 },
                    bodyFont: { family: 'monospace', size: 11 },
                    callbacks: {
                        title: function(items) {
                            if (!items.length) return '';
                            const d = items[0].raw.deck;
                            return d.deck_name || 'Commander Deck';
                        },
                        label: function(context) {
                            const raw = context.raw;
                            const d = raw.deck;
                            const cmdr = d.commander_name || (Array.isArray(d.commander) ? d.commander.join(', ') : d.commander) || 'Commander';
                            const lines = [
                                `Commander: ${cmdr}`,
                                `Speed (Avg Nonland CMC): ${raw.x.toFixed(2)}`,
                                `${metric === 'power' ? 'Power' : (metric === 'value' ? 'Value' : 'Synergy')}: ${metric === 'value' ? '$' + raw.y.toFixed(2) : raw.y}`,
                                `Total Value: $${raw.rawVal.toFixed(2)}`,
                                `⚡ Click bubble to inspect deck`
                            ];
                            return lines;
                        }
                    }
                }
            },
            scales: {
                x: {
                    title: {
                        display: true,
                        text: 'Deck Speed: Avg Nonland CMC (← Faster / Lower Curve)',
                        color: '#94a3b8',
                        font: { family: 'monospace', size: 11, weight: 'bold' }
                    },
                    grid: { color: 'rgba(38, 50, 69, 0.4)' },
                    ticks: { color: '#94a3b8', font: { family: 'monospace', size: 10 } }
                },
                y: yAxisConfig
            },
            onClick: function(event, elements) {
                if (elements && elements.length > 0) {
                    const idx = elements[0].index;
                    const point = dataPoints[idx];
                    if (point && point.deck && point.deck.id) {
                        loadSavedDeckIntoInspector(point.deck.id);
                    }
                }
            }
        }
    });
}

function setFleetChartMetric(metric) {
    currentFleetChartMetric = metric;
    ['power', 'value', 'synergy'].forEach(m => {
        const btn = document.getElementById(`btnFleetMetric${m.charAt(0).toUpperCase() + m.slice(1)}`);
        if (!btn) return;
        if (m === metric) {
            btn.className = "px-2.5 py-1 rounded-none text-cyan-400 bg-cyan-500/20 font-bold transition";
        } else {
            btn.className = "px-2.5 py-1 rounded-none text-slate-400 hover:text-white transition";
        }
    });
    renderFleetPositioningChart(allDecks, metric);
}

// Watchlist Button Handler for single cards
async function handleWatchClick(btn) {
    const name = btn.getAttribute('data-name');
    const setCode = btn.getAttribute('data-set') || '';
    if (!name) return;

    btn.disabled = true;
    const origHtml = btn.innerHTML;
    btn.innerHTML = `<svg class="animate-spin w-3 h-3 text-cyan-400" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>`;

    try {
        const resp = await fetch('/api/watchlist/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                set_code: setCode || null,
                is_any_version: !setCode || setCode === 'ANY',
                target_price: 1.0,
                tag: activeDeck ? activeDeck.deck_name : 'Commander'
            })
        });
        const data = await resp.json();
        if (resp.ok) {
            showToast(`Added '${name}' to your Watchlist!`, 'success');
            btn.className = "watch-card-btn px-2.5 py-1 bg-emerald-500/20 text-emerald-300 rounded-none text-[10px] font-mono uppercase font-bold border border-emerald-500/40 transition flex items-center gap-1 mx-auto";
            btn.innerHTML = `<span>✓ Watched</span>`;
        } else if (resp.status === 409) {
            showToast(`'${name}' is already on your Watchlist!`, 'info');
            btn.className = "watch-card-btn px-2.5 py-1 bg-slate-800 text-slate-400 rounded-none text-[10px] font-mono uppercase font-bold border border-[#263245] transition flex items-center gap-1 mx-auto";
            btn.innerHTML = `<span>✓ In List</span>`;
        } else {
            showToast(data.error || 'Failed to add card to watchlist.', 'error');
            btn.disabled = false;
            btn.innerHTML = origHtml;
        }
    } catch (e) {
        showToast(`Watchlist error: ${e.message}`, 'error');
        btn.disabled = false;
        btn.innerHTML = origHtml;
    }
}

// Tactical Quick Add Card Handler
async function executeQuickAddCard() {
    if (!activeDeck || !activeDeck.id) {
        showToast("Please open a saved deck from your Vault first to add cards.", "error");
        return;
    }

    const input = document.getElementById('quickAddCardInput');
    if (!input) return;
    const cardName = (input.value || '').trim();
    if (!cardName) {
        showToast("Please enter a card name to add.", "info");
        return;
    }

    const btn = document.getElementById('btnQuickAddCard');
    const origHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="inline-block animate-spin mr-1">⟳</span> Adding...';
    }

    try {
        const resp = await fetch(`/api/deck/${activeDeck.id}/add-card`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ card_name: cardName })
        });

        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.error || "Failed to add card to deck.");
        }

        input.value = '';
        activeDeck = data.deck;
        displayDeckInInspector(activeDeck, true);
        showToast(data.message || `Added '${cardName}' to deck!`, "success");

        // If upgrades tab is loaded, reload it
        if (currentInspectorTab === 'upgrades') {
            await loadDualTierUpgrades(activeDeck.id);
        }
    } catch (err) {
        showToast(`Failed to add card: ${err.message}`, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = origHtml;
        }
    }
}

// Cut / Remove Card Execution
async function executeRemoveCard(cardName, removeAll = false) {
    if (!activeDeck || !activeDeck.id) {
        showToast("No active deck loaded in inspector.", "error");
        return;
    }
    if (!cardName) return;

    try {
        const resp = await fetch(`/api/deck/${activeDeck.id}/remove-card`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ card_name: cardName, all: removeAll })
        });

        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.error || "Failed to remove card from deck.");
        }

        activeDeck = data.deck;

        // Keep local vault cache synchronized
        if (Array.isArray(currentVaultDecks)) {
            const vIdx = currentVaultDecks.findIndex(d => d.id === data.deck.id);
            if (vIdx !== -1) {
                currentVaultDecks[vIdx] = { ...currentVaultDecks[vIdx], ...data.deck };
            }
        }

        displayDeckInInspector(activeDeck, true);
        showToast(data.message || `Removed '${cardName}' from deck.`, "success");

        // If upgrades tab is loaded, re-evaluate
        if (currentInspectorTab === 'upgrades') {
            await loadDualTierUpgrades(activeDeck.id);
        }
    } catch (err) {
        showToast(`Failed to cut card: ${err.message}`, "error");
    }
}

function handleCutCardClick(btn) {
    const cardName = btn.getAttribute('data-card-name');
    if (!cardName) return;
    executeRemoveCard(cardName);
}

// Watchlist Button Handler for AI upgrades
async function handleUpgradeWatchClick(btn) {
    const name = btn.getAttribute('data-name');
    if (!name) return;

    btn.disabled = true;
    const origText = btn.innerText;
    btn.innerText = "Adding...";

    try {
        const resp = await fetch('/api/watchlist/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                is_any_version: true,
                target_price: 1.0,
                tag: activeDeck ? activeDeck.deck_name : 'Commander Upgrade'
            })
        });
        const data = await resp.json();
        if (resp.ok) {
            showToast(`Added upgrade '${name}' to Watchlist!`, 'success');
            btn.className = "w-full py-1.5 bg-emerald-500/20 text-emerald-300 text-xs font-mono font-bold uppercase rounded-none border border-emerald-500/40 text-center";
            btn.innerText = "✓ Added to Watchlist";
        } else if (resp.status === 409) {
            showToast(`'${name}' is already on your Watchlist!`, 'info');
            btn.className = "w-full py-1.5 bg-slate-800 text-slate-400 text-xs font-mono font-bold uppercase rounded-none border border-[#263245] text-center";
            btn.innerText = "✓ Already in Watchlist";
        } else {
            showToast(data.error || 'Failed to add upgrade', 'error');
            btn.disabled = false;
            btn.innerText = origText;
        }
    } catch (e) {
        showToast(`Watchlist error: ${e.message}`, 'error');
        btn.disabled = false;
        btn.innerText = origText;
    }
}

function stripDiacritics(str) {
    return (str || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
}

// Render AI Strategic Analysis
function renderAiAnalysis(analysis) {
    if (!analysis) return;
    document.getElementById('aiSummaryText').innerText = analysis.overall_summary || "Strategic analysis complete.";
    document.getElementById('aiManaBaseText').innerText = analysis.mana_base_analysis || "Balanced land and ramp acceleration profile.";

    const modelBadge = document.getElementById('aiActiveModelBadge');
    if (modelBadge && activeDeck) {
        modelBadge.innerText = activeDeck.model_used || (analysis && analysis._model_used) || 'gemini-3.8-flash';
    }

    // Win conditions
    const winConsContainer = document.getElementById('aiWinConsList');
    if (winConsContainer) {
        winConsContainer.innerHTML = (analysis.win_conditions || []).map(wc => {
            const keyCardsStr = Array.isArray(wc.key_cards) ? wc.key_cards.join(', ') : (wc.key_cards ? String(wc.key_cards) : '');
            return `
            <div class="bg-[#1B2230]/70 p-3 rounded-none border border-[#263245] space-y-1.5">
                <div class="flex items-center justify-between">
                    <span class="text-xs font-bold text-white uppercase">${escapeHtml(wc.title || 'Win Condition')}</span>
                    <span class="text-[10px] px-2 py-0.5 rounded-none bg-amber-500/20 text-amber-400 border border-amber-500/30">${escapeHtml(wc.type || 'Combat')}</span>
                </div>
                <p class="text-xs text-slate-300 font-sans">${escapeHtml(wc.description || '')}</p>
                ${keyCardsStr ? `<div class="text-[11px] text-cyan-400 font-mono pt-1">Key Cards: ${escapeHtml(keyCardsStr)}</div>` : ''}
            </div>
            `;
        }).join('');
    }

    // Upgrades
    const upgradesContainer = document.getElementById('aiUpgradesGrid');
    const upgradesList = analysis.upgrades || [];
    if (upgradesContainer) {
        if (!upgradesList || upgradesList.length === 0) {
            upgradesContainer.innerHTML = `
                <div class="col-span-1 md:col-span-2 py-8 text-center text-slate-400 font-mono text-xs bg-[#10141D] border border-[#263245] rounded-none">
                    No direct card cuts were proposed by Gemini for this decklist. Explore the <strong>Tactical Upgrades</strong> tab to cross-reference your Collection Binder and view market staples.
                </div>
            `;
        } else {
            upgradesContainer.innerHTML = upgradesList.map(u => {
                const inPriceNum = parseFloat(u.card_in_price);
                const priceBadge = (!isNaN(inPriceNum) && inPriceNum > 0) ? `<span class="text-xs font-mono font-bold text-emerald-400">$${inPriceNum.toFixed(2)}</span>` : '';
                return `
                <div class="bg-[#1B2230]/80 p-4 rounded-none border border-[#263245] flex flex-col justify-between space-y-3.5 shadow-lg relative overflow-hidden">
                    <div class="flex items-start justify-between gap-2">
                        <div class="flex items-center gap-1.5 flex-wrap">
                            <span class="text-[10px] px-1.5 py-0.5 rounded-none bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 uppercase font-bold font-mono">
                                ${escapeHtml(u.category || 'AI Upgrade')}
                            </span>
                            <span class="text-[10px] text-slate-400 font-mono">
                                Impact: <strong class="text-emerald-400">${escapeHtml(u.estimated_impact || 'High')}</strong>
                            </span>
                        </div>
                        ${priceBadge}
                    </div>

                    <!-- Swap Comparison Cards -->
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
                        <!-- IN: Addition -->
                        <div class="bg-emerald-950/25 border border-emerald-500/30 p-2.5 rounded-none space-y-1">
                            <span class="text-[10px] text-emerald-400 uppercase font-bold font-mono block">&darr; IN (RECOMMENDED)</span>
                            <div class="flex items-center gap-2">
                                ${u.card_in_image ? `
                                    <img src="${escapeHtml(toScryfallThumb(u.card_in_image))}" alt="${escapeHtml(u.card_in)}" loading="lazy" decoding="async" class="w-7 h-10 object-cover rounded-none border border-emerald-500/40 flex-shrink-0 cursor-pointer shadow"
                                         data-img="${escapeHtml(u.card_in_image)}"
                                         onclick="showCardHover(event, this)"
                                         onmouseenter="showCardHover(event, this)" onmousemove="moveCardHover(event)" onmouseleave="hideCardHover()">
                                ` : ''}
                                <div class="min-w-0 flex-1">
                                    <div class="font-bold text-white truncate hover:text-emerald-300 transition cursor-pointer" title="${escapeHtml(u.card_in)}"
                                         data-img="${escapeHtml(u.card_in_image || '')}"
                                         onclick="showCardHover(event, this)"
                                         onmouseenter="showCardHover(event, this)" onmousemove="moveCardHover(event)" onmouseleave="hideCardHover()">
                                        ${escapeHtml(u.card_in || '')}
                                    </div>
                                    <div class="text-[10px] text-slate-400 font-mono">
                                        ${u.card_in_mana ? `<span>${escapeHtml(u.card_in_mana)}</span> &bull; ` : ''}
                                        <span>${escapeHtml(u.card_in_type || 'Card')}</span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- OUT: Cut -->
                        <div class="bg-rose-950/25 border border-rose-500/30 p-2.5 rounded-none space-y-1">
                            <span class="text-[10px] text-rose-400 uppercase font-bold font-mono block">&uarr; OUT (RECOMMENDED CUT)</span>
                            <div class="flex items-center gap-2">
                                ${u.card_out_image ? `
                                    <img src="${escapeHtml(toScryfallThumb(u.card_out_image))}" alt="${escapeHtml(u.card_out)}" loading="lazy" decoding="async" class="w-7 h-10 object-cover rounded-none border border-rose-500/40 flex-shrink-0 cursor-pointer shadow"
                                         data-img="${escapeHtml(u.card_out_image)}"
                                         onclick="showCardHover(event, this)"
                                         onmouseenter="showCardHover(event, this)" onmousemove="moveCardHover(event)" onmouseleave="hideCardHover()">
                                ` : ''}
                                <div class="min-w-0 flex-1">
                                    <div class="font-bold text-slate-300 truncate hover:text-rose-300 transition cursor-pointer" title="${escapeHtml(u.card_out)}"
                                         data-img="${escapeHtml(u.card_out_image || '')}"
                                         onclick="showCardHover(event, this)"
                                         onmouseenter="showCardHover(event, this)" onmousemove="moveCardHover(event)" onmouseleave="hideCardHover()">
                                        ${escapeHtml(u.card_out || 'Suboptimal Card')}
                                    </div>
                                    <div class="text-[10px] text-slate-500 font-mono">
                                        ${u.card_out_mana ? `<span>${escapeHtml(u.card_out_mana)}</span>` : 'Cut candidate'}
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <p class="text-[11px] text-slate-300 font-sans leading-relaxed">${escapeHtml(u.rationale || '')}</p>

                    <!-- Action Buttons: Apply Swap and Track in Watchlist -->
                    <div class="flex items-center gap-2 pt-1">
                        <button type="button" onclick="handleApplySwapClick(this)" data-out="${escapeHtml(u.card_out || '')}" data-in="${escapeHtml(u.card_in || '')}" 
                                class="flex-1 py-1.5 bg-emerald-500 hover:bg-emerald-600 text-slate-950 text-xs font-mono font-bold uppercase rounded-none transition text-center shadow flex items-center justify-center gap-1.5">
                            <span>Apply Swap to Deck</span>
                            <span>&rarr;</span>
                        </button>
                        <button type="button" onclick="handleUpgradeWatchClick(this)" data-name="${escapeHtml(u.card_in || '')}" 
                                class="px-3 py-1.5 bg-cyan-500/10 hover:bg-cyan-500 text-cyan-400 hover:text-slate-950 text-xs font-mono font-bold uppercase rounded-none border border-cyan-500/30 transition text-center">
                            + Watchlist
                        </button>
                    </div>
                </div>
                `;
            }).join('');
        }
    }

    // Ratings
    renderCardRatings(analysis.card_ratings || []);
}

function renderCardRatings(ratings) {
    const tbody = document.getElementById('aiCardRatingsBody');
    if (!tbody) return;
    tbody.innerHTML = (ratings || []).map(r => {
        const score = parseFloat(r.rating || 7.0) || 7.0;
        let colorClass = "text-amber-400";
        if (score >= 9.0) colorClass = "text-cyan-400";
        else if (score >= 7.0) colorClass = "text-blue-400";
        else if (score < 5.0) colorClass = "text-rose-400";

        return `
            <tr class="hover:bg-[#1B2230]/50 transition rating-row" data-rating="${score}">
                <td class="py-2.5 px-3 font-black ${colorClass} text-sm whitespace-nowrap">${score.toFixed(1)}</td>
                <td class="py-2.5 px-3 font-semibold text-white whitespace-nowrap">${escapeHtml(r.card_name || '')}</td>
                <td class="py-2.5 px-3 text-slate-400 text-xs whitespace-nowrap">${escapeHtml(r.role || 'Utility')}</td>
                <td class="py-2.5 px-3 text-slate-300 font-sans text-xs min-w-[220px] max-w-[380px] break-words">${escapeHtml(r.purpose || '')}</td>
            </tr>
        `;
    }).join('');
}

function filterCardRatings() {
    const val = document.getElementById('ratingFilterSelect').value;
    const rows = document.querySelectorAll('.rating-row');
    rows.forEach(r => {
        const score = parseFloat(r.getAttribute('data-rating') || 0);
        if (val === 'all') r.style.display = '';
        else if (val === '9') r.style.display = score >= 9.0 ? '' : 'none';
        else if (val === '7') r.style.display = (score >= 7.0 && score < 9.0) ? '' : 'none';
        else if (val === '5') r.style.display = (score >= 5.0 && score < 7.0) ? '' : 'none';
        else if (val === 'cuts') r.style.display = score < 5.0 ? '' : 'none';
    });
}

// Ingestion Handlers
async function executeParseAndSave(runAi = false) {
    let source = '';
    let sourceType = 'auto';

    if (currentIngestTab === 'url') {
        source = document.getElementById('deckUrlInput').value.trim();
        sourceType = 'auto';
    } else if (currentIngestTab === 'text') {
        source = document.getElementById('deckTextInput').value.trim();
        sourceType = 'text';
    } else if (currentIngestTab === 'csv') {
        source = window.uploadedCsvContent || '';
        sourceType = 'csv';
    }

    if (!source) {
        showToast("Please provide a ManaBox deck link or card list.", "error");
        return;
    }

    const btn = runAi ? document.getElementById('parseAnalyzeBtn') : document.getElementById('parseOnlyBtn');
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin h-4 w-4 text-current inline" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> <span>Processing...</span>`;

    if (runAi) dismissAiErrorAlert();

    const formatChoice = document.querySelector('input[name="deckFormatChoice"]:checked')?.value || 'commander';
    const isPauper = formatChoice === 'pauper_commander';

    try {
        if (runAi) {
            const modelSelect = document.getElementById('savedDeckAiModel') || document.getElementById('modalGeminiModel');
            const model = modelSelect ? modelSelect.value : undefined;
            const reqBody = { 
                source, 
                source_type: sourceType, 
                save: true,
                is_pauper: isPauper,
                deck_format: formatChoice
            };
            if (model) reqBody.model = model;

            const resp = await fetch('/api/deck/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(reqBody)
            });
            let data = null;
            try {
                data = await resp.json();
            } catch (jsonErr) {
                // e.g. proxy or gateway timeout returned HTML
            }

            if (!resp.ok || !data || !data.success) {
                // If initial request was severed or timed out, check if server completed in background
                const recovered = await pollForNewestDeckAnalysis(source, btn, 48);
                if (recovered) return;
                throw new Error((data && data.error) || (resp.status ? `Server returned HTTP ${resp.status}` : "Analysis timed out on gateway"));
            }

            showToast("Deck analyzed and saved to Vault!", "success");
            dismissAiErrorAlert();
            await refreshVaultList();
            try {
                displayDeckInInspector(data.deck ? { ...data.deck, analysis: data.analysis, has_ai_analysis: true } : data);
            } catch (renderErr) {
                console.warn("Inspector render warning:", renderErr);
            }
        } else {
            const resp = await fetch('/api/deck/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    source, 
                    source_type: sourceType,
                    is_pauper: isPauper,
                    deck_format: formatChoice
                })
            });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.error || "Failed to save deck");
            showToast("Deck saved to Vault with Scryfall stats!", "success");
            await refreshVaultList();
            displayDeckInInspector(data.deck);
        }
    } catch (e) {
        if (runAi) {
            const recovered = await pollForNewestDeckAnalysis(source, btn, 48);
            if (recovered) return;
            const isFetchErr = e.message && e.message.toLowerCase().includes("fetch");
            const alertMsg = isFetchErr
                ? "The AI analysis is taking longer than usual on the server. The process may still be running in the background—please check your Vault in a moment or refresh the page."
                : e.message;
            showAiErrorAlert(alertMsg);
        }
        showToast(`Error: ${e.message}`, "error");
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

function handleDeckSubmit(e) {
    e.preventDefault();
    executeParseAndSave(true);
}

// Bulk Import Handlers
function openBulkImportModal() {
    document.getElementById('bulkImportModal').classList.remove('hidden');
}
function closeBulkImportModal() {
    document.getElementById('bulkImportModal').classList.add('hidden');
}

async function executeBulkImport() {
    const text = document.getElementById('bulkUrlsInput').value.trim();
    if (!text) {
        showToast("Please enter at least one ManaBox link.", "error");
        return;
    }

    const btn = document.getElementById('executeBulkImportBtn');
    const progress = document.getElementById('bulkImportProgress');
    btn.disabled = true;
    progress.classList.remove('hidden');

    try {
        const resp = await fetch('/api/deck/bulk-import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text })
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error || "Bulk import failed");

        showToast(`Successfully imported ${data.imported_count} decks!`, 'success');
        closeBulkImportModal();
        await refreshVaultList();
        switchMainView('vault');
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    } finally {
        btn.disabled = false;
        progress.classList.add('hidden');
    }
}

// Sync Deck from Source
async function syncSavedDeck(deckId) {
    try {
        const resp = await fetch(`/api/deck/${deckId}/sync`, { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error || "Sync failed");
        showToast(data.message || "Deck refreshed with latest cards!", "success");
        await refreshVaultList();
        return data.deck;
    } catch (e) {
        showToast(`Sync error: ${e.message}`, "error");
        throw e;
    }
}

async function syncActiveDeckFromSource() {
    if (!activeDeck || !activeDeck.id) return;
    const btn = document.getElementById('inspectorSyncBtn');
    if (btn) btn.classList.add('animate-spin');
    try {
        const updatedDeck = await syncSavedDeck(activeDeck.id);
        if (updatedDeck) {
            displayDeckInInspector(updatedDeck);
        } else {
            await loadSavedDeckIntoInspector(activeDeck.id);
        }
    } catch (e) {
        // Handled in syncSavedDeck
    } finally {
        if (btn) btn.classList.remove('animate-spin');
    }
}

// Polling recovery for saved deck analysis when network or gateway times out
async function pollForCompletedAnalysis(deckId, btn, maxAttempts = 48, initialUpdatedAt = null) {
    if (!deckId) return false;
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        await new Promise(r => setTimeout(r, 2500));
        try {
            if (btn) btn.innerText = `Analyzing with Gemini (${Math.round(attempt * 2.5)}s)...`;
            const checkResp = await fetch(`/api/deck/history/${deckId}`);
            if (checkResp.ok) {
                const freshDeck = await checkResp.json();
                const isUpdated = initialUpdatedAt ? (freshDeck.updated_at !== initialUpdatedAt) : true;
                if (freshDeck && freshDeck.has_ai_analysis && freshDeck.analysis && Object.keys(freshDeck.analysis).length > 0 && isUpdated) {
                    showToast("Gemini analysis complete!", "success");
                    dismissAiErrorAlert();
                    try {
                        displayDeckInInspector(freshDeck, true);
                    } catch (renderErr) {
                        console.warn("Inspector render warning:", renderErr);
                    }
                    await refreshVaultList();
                    return true;
                }
            }
        } catch (pollErr) {
            console.warn("Analysis polling attempt error:", pollErr);
        }
    }
    return false;
}

// Polling recovery for newly parsed & analyzed deck imports
async function pollForNewestDeckAnalysis(source, btn, maxAttempts = 48) {
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        await new Promise(r => setTimeout(r, 2500));
        try {
            if (btn) btn.innerHTML = `<svg class="animate-spin h-4 w-4 text-current inline" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> <span>Analyzing with Gemini (${Math.round(attempt * 2.5)}s)...</span>`;
            const histResp = await fetch('/api/deck/history');
            if (histResp.ok) {
                const list = await histResp.json();
                if (list && list.length > 0) {
                    const latest = list[0];
                    if (latest && latest.has_ai_analysis) {
                        const fullResp = await fetch(`/api/deck/history/${latest.id}`);
                        if (fullResp.ok) {
                            const fullDeck = await fullResp.json();
                            showToast("Deck analyzed and saved to Vault!", "success");
                            dismissAiErrorAlert();
                            await refreshVaultList();
                            try {
                                displayDeckInInspector(fullDeck);
                            } catch (renderErr) {
                                console.warn("Inspector render warning:", renderErr);
                            }
                            return true;
                        }
                    }
                }
            }
        } catch (pollErr) {
            console.warn("Analysis polling attempt error:", pollErr);
        }
    }
    return false;
}

// Run AI on pre-saved deck (Initial or Rerun)
async function runAiAnalysisOnSavedDeck(callingBtn = null) {
    if (!activeDeck || !activeDeck.id) return;
    dismissAiErrorAlert();

    // Determine model from rerun selector if available, or savedDeckAiModel
    const rerunSelect = document.getElementById('rerunDeckAiModel');
    const initSelect = document.getElementById('savedDeckAiModel');
    const model = (rerunSelect && rerunSelect.value) || (initSelect && initSelect.value) || 'gemini-3.8-flash';

    const triggerButtons = [
        document.getElementById('runSavedAiBtn'),
        document.getElementById('btnRerunAiIntel'),
        document.getElementById('inspectorRerunAiBtn')
    ].filter(Boolean);

    triggerButtons.forEach(b => {
        b.disabled = true;
        b.dataset.origHtml = b.innerHTML;
        b.innerHTML = `<svg class="animate-spin h-3.5 w-3.5 text-current inline mr-1" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg><span>Analyzing...</span>`;
    });

    const initialUpdatedAt = activeDeck.updated_at || null;
    const pollingBtn = callingBtn || document.getElementById('btnRerunAiIntel') || document.getElementById('runSavedAiBtn');

    try {
        const resp = await fetch(`/api/deck/${activeDeck.id}/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model })
        });
        
        let data = null;
        try {
            data = await resp.json();
        } catch (parseErr) {
            // e.g. gateway timeout or proxy 504 returned HTML instead of JSON
        }

        if (!resp.ok || !data || !data.success) {
            // Check if backend completed or is completing in the background before reporting error
            const recovered = await pollForCompletedAnalysis(activeDeck.id, pollingBtn, 48, initialUpdatedAt);
            if (recovered) return;
            throw new Error((data && data.error) || (resp.status ? `Server returned HTTP ${resp.status}` : "Analysis request timed out on gateway"));
        }

        showToast("Gemini analysis complete!", "success");
        dismissAiErrorAlert();
        try {
            displayDeckInInspector(data.deck, true);
        } catch (renderErr) {
            console.warn("Inspector render error:", renderErr);
        }
        await refreshVaultList();
    } catch (e) {
        // If a network disconnect or timeout occurred, poll server to check if analysis completes
        const recovered = await pollForCompletedAnalysis(activeDeck.id, pollingBtn, 48, initialUpdatedAt);
        if (recovered) return;
        const isFetchErr = e.message && e.message.toLowerCase().includes("fetch");
        const alertMsg = isFetchErr
            ? "The AI analysis is taking longer than usual on the server. The process may still be running in the background—please check your Vault in a moment or refresh the page."
            : e.message;
        showAiErrorAlert(alertMsg);
        showToast(`AI error: ${e.message}`, "error");
    } finally {
        triggerButtons.forEach(b => {
            b.disabled = false;
            if (b.id === 'runSavedAiBtn') {
                b.innerHTML = '⚡ Analyze Now';
            } else if (b.id === 'btnRerunAiIntel') {
                b.innerHTML = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg><span>⚡ Rerun AI Intel</span>';
            } else if (b.id === 'inspectorRerunAiBtn') {
                b.innerHTML = '<span>⚡</span><span class="hidden md:inline">Rerun Intel</span>';
            } else if (b.dataset.origHtml) {
                b.innerHTML = b.dataset.origHtml;
            }
        });
    }
}

// Delete Deck
function handleDeleteDeckClick(deckId) {
    const deck = currentVaultDecks.find(d => d.id === deckId);
    const deckName = deck ? deck.deck_name : 'this deck';
    if (!confirm(`Are you sure you want to delete '${deckName}' from your Vault?`)) return;
    deleteSavedDeck(deckId, deckName);
}

async function deleteSavedDeck(deckId, deckName) {
    try {
        const resp = await fetch(`/api/deck/history/${deckId}`, { method: 'DELETE' });
        if (!resp.ok) throw new Error("Delete failed");
        showToast(`Deck '${deckName}' removed from Vault.`, "success");
        await refreshVaultList();
        if (activeDeck && activeDeck.id === deckId) {
            switchMainView('vault');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, "error");
    }
}

// Refresh Vault List
async function refreshVaultList() {
    try {
        const resp = await fetch('/api/deck/history');
        if (resp.ok) {
            currentVaultDecks = await resp.json();
            filterVaultDecks();
            updateDeckDropdowns(currentVaultDecks);
            if (activeDeck && activeDeck.id) {
                const quickSel = document.getElementById('quickDeckSelect');
                if (quickSel) quickSel.value = activeDeck.id;
                const inspSel = document.getElementById('inspectorDeckSelect');
                if (inspSel) inspSel.value = activeDeck.id;
            }
        }
    } catch (e) {
        console.error("Failed to refresh vault list", e);
    }
}

// =========================================================================
// COMPARATIVE MATRIX TABLE & FILTERING
// =========================================================================

// Render Matrix Table
function renderMatrixTable(decks) {
    const tbody = document.getElementById('matrixTableBody');
    const emptyState = document.getElementById('matrixEmptyState');

    if (!decks || decks.length === 0) {
        if (tbody) tbody.innerHTML = '';
        if (emptyState) emptyState.classList.remove('hidden');
        return;
    }

    if (emptyState) emptyState.classList.add('hidden');
    if (!tbody) return;

    const colorsMap = { 'W': '#FFFBD5', 'U': '#0E68AB', 'B': '#150B00', 'R': '#D3202A', 'G': '#00733E' };

    tbody.innerHTML = decks.map(d => {
        const isSelected = selectedDeckIds.has(d.id);
        const stats = d.stats || {};
        const val = (d.total_value !== null && d.total_value !== undefined) ? d.total_value : (stats.total_value || 0);
        const cmc = (d.avg_cmc !== null && d.avg_cmc !== undefined) ? d.avg_cmc : (stats.avg_cmc || 0);
        const pwr = d.power_level ? d.power_level.toFixed(1) : null;
        const art = d.commander_art || 'https://cards.scryfall.io/art_crop/front/b/4/b43e23b4-e699-4524-b6a5-30b14c330f60.jpg';

        const colorPips = (d.color_identity || stats.color_identity || []).map(c => 
            `<span class="inline-block w-2.5 h-2.5 rounded-full border border-[#263245]" style="background-color: ${colorsMap[c] || '#888'};"></span>`
        ).join('');

        // Type breakdown mini segments
        const typeCounts = stats.type_counts || {};
        const lands = typeCounts['Lands'] || 0;
        const total = d.total_cards || 100;
        const spells = Math.max(0, total - lands);
        const landPct = Math.round((lands / total) * 100);
        const spellPct = 100 - landPct;

        const keepRate = d.effective_keepability_rate !== undefined ? d.effective_keepability_rate : (stats.effective_keepability_rate || (stats.opening_hand_keepability && stats.opening_hand_keepability.effective_keep_rate) || 0);
        const castTurn = d.median_commander_cast_turn !== undefined ? d.median_commander_cast_turn : (stats.median_commander_cast_turn || (stats.earliest_commander_cast && stats.earliest_commander_cast.median_cast_turn) || 4);
        const wincon = d.primary_wincon || stats.primary_wincon || (stats.win_conditions && stats.win_conditions.primary_wincon && stats.win_conditions.primary_wincon.name) || (d.archetype || 'Combat');
        const sinks = d.total_mana_sinks !== undefined ? d.total_mana_sinks : (stats.total_mana_sinks || (stats.mana_sinks && stats.mana_sinks.total_sinks) || 0);
        const rawDate = d.updated_at || d.created_at || (stats && stats.last_synced_at);
        const relDate = formatRelativeDate(rawDate);

        return `
            <tr class="hover:bg-[#1B2230]/60 transition ${isSelected ? 'bg-cyan-950/20' : ''}" id="matrix-row-${d.id}">
                <td class="py-3 px-3.5 text-center">
                    <input type="checkbox" onchange="toggleDeckSelection(${d.id}, this.checked)" ${isSelected ? 'checked' : ''}
                           class="rounded-none border-[#263245] bg-slate-900 text-cyan-500 focus:ring-0 cursor-pointer">
                </td>
                <td class="py-3 px-4">
                    <div class="flex items-center space-x-3">
                        <img src="${art}" alt="${escapeHtml(d.commander_name || 'Commander')}" loading="lazy" decoding="async" class="w-10 h-10 rounded-none object-cover border border-[#263245] flex-shrink-0 bg-slate-900">
                        <div class="min-w-0">
                            <div class="font-bold text-white uppercase truncate flex items-center gap-2">
                                <span>${escapeHtml(d.deck_name)}</span>
                                ${d.is_pauper ? `<span class="text-[9px] px-1.5 py-0.2 rounded-none bg-purple-900/80 border border-purple-500/60 text-purple-200 font-mono font-bold">PDH</span>` : `<span class="text-[9px] px-1.5 py-0.2 rounded-none bg-slate-800 text-slate-400 font-mono">EDH</span>`}
                                ${relDate ? `<span class="text-[9px] px-1.5 py-0.2 rounded-none bg-slate-800 text-slate-400 font-mono font-normal">📅 ${relDate}</span>` : ''}
                            </div>
                            <div class="text-[11px] text-cyan-300 font-normal truncate">${escapeHtml(d.commander_name || 'Commander')}</div>
                        </div>
                    </div>
                </td>
                <td class="py-3 px-3 text-center cursor-help" data-tooltip-key="col_colors">
                    <div class="flex items-center justify-center gap-1">
                        ${colorPips || '<span class="text-[10px] text-slate-500 font-mono">Colorless</span>'}
                    </div>
                </td>
                <td class="py-3 px-3 text-center cursor-help" data-tooltip-key="power_level">
                    ${pwr ? `
                        <div class="inline-flex items-center gap-1 px-2 py-0.5 rounded-none bg-cyan-500/20 text-cyan-400 border border-cyan-500/40 font-bold text-xs">
                            <span>⚡ ${pwr}</span>
                        </div>
                    ` : `
                        <span class="text-[10px] px-1.5 py-0.2 rounded-none bg-slate-800 text-slate-400 border border-[#263245]">Pre-AI</span>
                    `}
                </td>
                <td class="py-3 px-4 text-right font-black text-emerald-400 text-sm cursor-help" data-tooltip-key="tcg_value">
                    $${parseFloat(val).toFixed(2)}
                </td>
                <td class="py-3 px-3 text-center font-bold text-amber-400 cursor-help" data-tooltip-key="amv">
                    ${parseFloat(cmc).toFixed(2)}
                </td>
                <td class="py-3 px-3 text-center cursor-help" data-tooltip-key="speed_pacing">
                    <div class="text-[11px] font-mono text-emerald-400 font-bold">${keepRate}% Keep</div>
                    <div class="text-[10px] font-mono text-slate-400">T${castTurn} Cast</div>
                </td>
                <td class="py-3 px-3 text-center cursor-help" data-tooltip-key="win_conditions">
                    <div class="text-[11px] font-mono text-cyan-300 font-bold truncate max-w-[120px] mx-auto" title="${escapeHtml(wincon)}">${escapeHtml(wincon)}</div>
                    <div class="text-[10px] font-mono text-slate-400">${sinks} Sinks</div>
                </td>
                <td class="py-3 px-4 text-center cursor-help" data-tooltip-key="deck_composition">
                    <div class="text-[11px] font-mono text-slate-300 mb-1">${lands}L / ${spells}S</div>
                    <div class="w-24 mx-auto h-1.5 bg-slate-800 rounded-full overflow-hidden flex">
                        <div class="bg-amber-500 h-full" style="width: ${landPct}%" title="${lands} Lands (${landPct}%)"></div>
                        <div class="bg-cyan-500 h-full" style="width: ${spellPct}%" title="${spells} Spells (${spellPct}%)"></div>
                    </div>
                </td>
                <td class="py-3 px-3 text-center cursor-help" data-tooltip-key="col_ai">
                    ${d.has_ai_analysis ? 
                        `<span class="text-[10px] px-1.5 py-0.2 rounded-none bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-bold">Analyzed</span>` : 
                        `<span class="text-[10px] px-1.5 py-0.2 rounded-none bg-slate-800 text-slate-400 border border-[#263245]">Stats Ready</span>`
                    }
                </td>
                <td class="py-3 px-4 text-right">
                    <div class="flex items-center justify-end gap-1.5">
                        <button onclick="syncSavedDeck(${d.id})" title="Re-sync deck list from ManaBox" class="p-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white rounded-none text-[10px] border border-[#263245] transition cursor-pointer">
                            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                        </button>
                        <button onclick="quickCompareWithFirst(${d.id})" title="Quick compare with Slot 1" class="px-2 py-1 bg-slate-800 hover:bg-cyan-500 hover:text-slate-950 text-cyan-400 rounded-none text-[10px] font-bold border border-[#263245] transition cursor-pointer">
                            VS
                        </button>
                        <button onclick="loadSavedDeckIntoInspector(${d.id}, 'upgrades')" title="View deck upgrades & binder swaps" class="px-2.5 py-1 bg-emerald-500/10 hover:bg-emerald-500 text-emerald-400 hover:text-slate-950 rounded-none text-[10px] font-bold border border-emerald-500/30 transition cursor-pointer">
                            Upgrades
                        </button>
                        <button onclick="loadSavedDeckIntoInspector(${d.id})" title="Inspect Deck" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-none text-[10px] font-bold border border-[#263245] transition cursor-pointer">
                            Inspect &rarr;
                        </button>
                        <button onclick="handleDeleteDeckClick(${d.id})" title="Delete deck from Vault" class="p-1.5 bg-rose-500/10 hover:bg-rose-500 text-rose-400 hover:text-white rounded-none text-[10px] border border-rose-500/30 transition cursor-pointer">
                            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

// Filter Vault Decks (for both Grid and Table modes)
function filterVaultDecks() {
    const input = document.getElementById('vaultSearchInput') || document.getElementById('matrixSearchInput');
    const query = (input ? input.value : '').trim().toLowerCase();
    const clearBtn = document.getElementById('btnClearVaultSearch');
    if (clearBtn) {
        if (query.length > 0) clearBtn.classList.remove('hidden');
        else clearBtn.classList.add('hidden');
    }

    const sourceDecks = (currentVaultDecks && currentVaultDecks.length > 0) ? currentVaultDecks : (allDecks || []);

    filteredDecks = sourceDecks.filter(d => {
        const matchesQuery = !query || 
                             (d.deck_name || '').toLowerCase().includes(query) || 
                             (d.commander_name || '').toLowerCase().includes(query) ||
                             (d.archetype || '').toLowerCase().includes(query);
        let matchesColor = true;
        if (currentColorFilter !== 'all') {
            const colors = d.color_identity || (d.stats && d.stats.color_identity) || [];
            matchesColor = colors.includes(currentColorFilter);
        }

        let matchesFormat = true;
        const isPauper = Boolean(d.is_pauper || (d.deck_format && d.deck_format.toLowerCase().includes('pauper')));
        if (currentFormatFilter === 'pauper_commander') {
            matchesFormat = isPauper;
        } else if (currentFormatFilter === 'commander') {
            matchesFormat = !isPauper;
        }

        return matchesQuery && matchesColor && matchesFormat;
    });

    sortMatrixInternal();
    renderVaultGrid(filteredDecks);
    renderMatrixTable(filteredDecks);
}

function filterMatrixTable() {
    filterVaultDecks();
}

function setColorFilter(color) {
    currentColorFilter = color;
    document.querySelectorAll('.color-filter-btn').forEach(btn => {
        if (btn.getAttribute('data-color') === color) {
            btn.className = "color-filter-btn px-1.5 sm:px-2 py-0.5 sm:py-1 text-[10px] sm:text-[11px] font-mono font-bold rounded-none bg-cyan-500 text-slate-950 cursor-pointer shadow-sm";
        } else {
            btn.className = "color-filter-btn px-1.5 sm:px-2 py-0.5 sm:py-1 text-[10px] sm:text-[11px] font-mono font-bold rounded-none bg-[#1B2230] text-slate-300 hover:text-white border border-[#263245] cursor-pointer";
        }
    });
    filterVaultDecks();
}

function setFormatFilter(fmt) {
    currentFormatFilter = fmt;
    document.querySelectorAll('.format-filter-btn').forEach(btn => {
        const btnFmt = btn.getAttribute('data-format');
        if (btnFmt === fmt) {
            if (fmt === 'pauper_commander') {
                btn.className = "format-filter-btn px-1.5 sm:px-2 py-0.5 sm:py-1 text-[10px] sm:text-[11px] font-mono font-bold rounded-none bg-purple-600 text-white shadow-sm cursor-pointer";
            } else {
                btn.className = "format-filter-btn px-1.5 sm:px-2 py-0.5 sm:py-1 text-[10px] sm:text-[11px] font-mono font-bold rounded-none bg-cyan-500 text-slate-950 font-bold shadow-sm cursor-pointer";
            }
        } else {
            if (btnFmt === 'pauper_commander') {
                btn.className = "format-filter-btn px-1.5 sm:px-2 py-0.5 sm:py-1 text-[10px] sm:text-[11px] font-mono font-bold rounded-none bg-purple-950/40 text-purple-300 hover:text-purple-100 border border-purple-500/40 transition cursor-pointer";
            } else {
                btn.className = "format-filter-btn px-1.5 sm:px-2 py-0.5 sm:py-1 text-[10px] sm:text-[11px] font-mono font-bold rounded-none bg-[#1B2230] text-slate-300 hover:text-white border border-[#263245] transition cursor-pointer";
            }
        }
    });
    filterVaultDecks();
}

function clearMatrixFilters() {
    const input = document.getElementById('vaultSearchInput') || document.getElementById('matrixSearchInput');
    if (input) input.value = '';
    const clearBtn = document.getElementById('btnClearVaultSearch');
    if (clearBtn) clearBtn.classList.add('hidden');
    currentColorFilter = 'all';
    currentFormatFilter = 'all';
    setColorFilter('all');
    setFormatFilter('all');
}


// Sorting
function sortVaultDecks() {
    const sort = document.getElementById('vaultSortSelect') ? document.getElementById('vaultSortSelect').value : 'recent';
    if (sort === 'name') {
        currentSortKey = 'commander';
        currentSortAsc = true;
    } else if (sort === 'price_desc') {
        currentSortKey = 'value';
        currentSortAsc = false;
    } else if (sort === 'cmc_asc') {
        currentSortKey = 'cmc';
        currentSortAsc = true;
    } else if (sort === 'power_desc') {
        currentSortKey = 'power';
        currentSortAsc = false;
    } else {
        currentSortKey = 'recent';
        currentSortAsc = false;
    }
    sortMatrixInternal();
    renderVaultGrid(filteredDecks);
    renderMatrixTable(filteredDecks);
}

function sortMatrixBy(key) {
    if (currentSortKey === key) {
        currentSortAsc = !currentSortAsc;
    } else {
        currentSortKey = key;
        currentSortAsc = false;
    }
    sortMatrixInternal();
    renderVaultGrid(filteredDecks);
    renderMatrixTable(filteredDecks);
}

function sortMatrixInternal() {
    filteredDecks.sort((a, b) => {
        let valA, valB;
        if (currentSortKey === 'commander') {
            valA = (a.deck_name || '').toLowerCase();
            valB = (b.deck_name || '').toLowerCase();
            return currentSortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
        } else if (currentSortKey === 'value') {
            valA = a.total_value || (a.stats && a.stats.total_value) || 0;
            valB = b.total_value || (b.stats && b.stats.total_value) || 0;
        } else if (currentSortKey === 'cmc') {
            valA = a.avg_cmc || (a.stats && a.stats.avg_cmc) || 0;
            valB = b.avg_cmc || (b.stats && b.stats.avg_cmc) || 0;
        } else if (currentSortKey === 'power') {
            valA = a.power_level || 0;
            valB = b.power_level || 0;
        } else if (currentSortKey === 'colors') {
            valA = (a.color_identity || []).length;
            valB = (b.color_identity || []).length;
        } else if (currentSortKey === 'composition') {
            valA = (a.stats && a.stats.type_counts && a.stats.type_counts['Lands']) || 0;
            valB = (b.stats && b.stats.type_counts && b.stats.type_counts['Lands']) || 0;
        } else if (currentSortKey === 'speed') {
            valA = a.effective_keepability_rate || (a.stats && a.stats.effective_keepability_rate) || 0;
            valB = b.effective_keepability_rate || (b.stats && b.stats.effective_keepability_rate) || 0;
        } else if (currentSortKey === 'wincon') {
            valA = (a.primary_wincon || (a.stats && a.stats.primary_wincon) || '').toLowerCase();
            valB = (b.primary_wincon || (b.stats && b.stats.primary_wincon) || '').toLowerCase();
            return currentSortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
        } else {
            valA = new Date(a.updated_at || a.created_at || 0).getTime();
            valB = new Date(b.updated_at || b.created_at || 0).getTime();
        }
        return currentSortAsc ? (valA - valB) : (valB - valA);
    });
}

// =========================================================================
// MULTI-DECK SELECTION & COMPARISON STATION
// =========================================================================

function toggleDeckSelection(deckId, isChecked) {
    if (isChecked) {
        if (selectedDeckIds.size >= 4) {
            showToast("You can compare up to 4 decks at once.", "info");
            renderMatrixTable(filteredDecks);
            return;
        }
        selectedDeckIds.add(deckId);
    } else {
        selectedDeckIds.delete(deckId);
    }
    updateSelectedCountUI();
    syncDropdownsWithSelection();
}

function toggleSelectAllDecks() {
    if (selectedDeckIds.size === filteredDecks.length) {
        selectedDeckIds.clear();
    } else {
        selectedDeckIds.clear();
        filteredDecks.slice(0, 4).forEach(d => selectedDeckIds.add(d.id));
    }
    updateSelectedCountUI();
    syncDropdownsWithSelection();
    renderMatrixTable(filteredDecks);
}

function updateSelectedCountUI() {
    const count = selectedDeckIds.size;
    const selectedCountText = document.getElementById('selectedCountText');
    if (selectedCountText) selectedCountText.innerText = count;

    const badge = document.getElementById('compareCountBadge');
    if (badge) {
        if (count > 0) {
            badge.innerText = count;
            badge.classList.remove('hidden');
        } else {
            badge.classList.add('hidden');
        }
    }
    const compareBtn = document.getElementById('launchCompareBtn');
    if (compareBtn) compareBtn.disabled = count < 2;
}

function syncDropdownsWithSelection() {
    const arr = Array.from(selectedDeckIds);
    for (let i = 1; i <= 4; i++) {
        const sel = document.getElementById(`compareSelect${i}`);
        if (sel) {
            sel.value = arr[i - 1] || '';
        }
    }
}

function handleDropdownDeckChange() {
    selectedDeckIds.clear();
    for (let i = 1; i <= 4; i++) {
        const sel = document.getElementById(`compareSelect${i}`);
        if (sel && sel.value) {
            selectedDeckIds.add(parseInt(sel.value, 10));
        }
    }
    updateSelectedCountUI();
    renderMatrixTable(filteredDecks);
    if (selectedDeckIds.size >= 2) {
        runActiveComparison();
    }
}

function launchComparisonFromSelection() {
    if (selectedDeckIds.size < 2) {
        showToast("Please select at least 2 decks to compare.", "info");
        return;
    }
    syncDropdownsWithSelection();
    switchMainView('compare');
    runActiveComparison();
}

function quickCompareWithFirst(deckId) {
    const sourceDecks = (allDecks && allDecks.length > 0) ? allDecks : currentVaultDecks;
    if (sourceDecks.length < 2) {
        showToast("Add at least 2 decks to compare matchups.", "info");
        return;
    }
    const firstDeckId = sourceDecks[0].id === deckId ? (sourceDecks[1] ? sourceDecks[1].id : sourceDecks[0].id) : sourceDecks[0].id;
    selectedDeckIds.clear();
    selectedDeckIds.add(firstDeckId);
    selectedDeckIds.add(deckId);
    syncDropdownsWithSelection();
    updateSelectedCountUI();
    renderMatrixTable(filteredDecks);
    switchMainView('compare');
    runActiveComparison();
}

function launchCompareFromActiveDeck() {
    if (!activeDeck || !activeDeck.id) return;
    const sourceDecks = (allDecks && allDecks.length > 0) ? allDecks : currentVaultDecks;
    if (sourceDecks.length < 2) {
        showToast("Add at least 2 decks to compare matchups.", "info");
        return;
    }
    const otherDeck = sourceDecks.find(d => d.id !== activeDeck.id) || sourceDecks[0];
    selectedDeckIds.clear();
    selectedDeckIds.add(activeDeck.id);
    if (otherDeck) selectedDeckIds.add(otherDeck.id);
    syncDropdownsWithSelection();
    updateSelectedCountUI();
    renderMatrixTable(filteredDecks);
    switchMainView('compare');
    runActiveComparison();
}

// Run Active Comparison Request to Server
async function runActiveComparison() {
    const deckIds = Array.from(selectedDeckIds);
    if (deckIds.length < 2) {
        showToast("Select at least 2 decks to compare.", "info");
        return;
    }

    const loader = document.getElementById('compareLoading');
    const container = document.getElementById('compareResultsContainer');
    if (loader) loader.classList.remove('hidden');
    if (container) container.classList.add('hidden');

    try {
        const resp = await fetch('/api/deck/compare', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ deck_ids: deckIds })
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error || "Comparison calculation failed");

        currentComparison = data;
        renderComparisonResults(data);
        if (loader) loader.classList.add('hidden');
        if (container) container.classList.remove('hidden');
    } catch (e) {
        if (loader) loader.classList.add('hidden');
        showToast(`Comparison Error: ${e.message}`, 'error');
    }
}

let comparePipRadarChartInstance = null;

// Render Complete Comparison View
function renderComparisonResults(data) {
    const decks = data.decks || [];
    const colorsMap = { 'W': '#FFFBD5', 'U': '#0E68AB', 'B': '#150B00', 'R': '#D3202A', 'G': '#00733E' };

    // 1. Commander Face-Off Cards
    const cmdrGrid = document.getElementById('compareCommanderCardsGrid');
    if (cmdrGrid) {
        cmdrGrid.className = `grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-${Math.min(decks.length, 4)} gap-4`;
        cmdrGrid.innerHTML = decks.map(d => {
            const colorPips = (d.color_identity || []).map(c => 
                `<span class="inline-block w-2.5 h-2.5 rounded-full border border-[#263245]" style="background-color: ${colorsMap[c] || '#888'};"></span>`
            ).join('');
            const art = d.commander_art || 'https://cards.scryfall.io/art_crop/front/b/4/b43e23b4-e699-4524-b6a5-30b14c330f60.jpg';

            return `
                <div class="bg-[#10141D] border border-[#263245] hover:border-cyan-500/40 rounded-none overflow-hidden shadow-2xl flex flex-col justify-between">
                    <div>
                        <div class="relative h-32 bg-slate-900 overflow-hidden">
                            <img src="${art}" alt="${escapeHtml(d.commander_name || 'Commander')}" loading="lazy" decoding="async" class="w-full h-full object-cover object-center">
                            <div class="absolute inset-0 bg-gradient-to-t from-[#10141D] via-[#10141D]/30 to-transparent"></div>
                            <div class="absolute bottom-2 left-3 right-3 flex items-center justify-between">
                                <span class="text-xs text-cyan-300 font-mono font-bold truncate">${escapeHtml(d.commander_name || 'Commander')}</span>
                                <div class="flex items-center gap-0.5 bg-slate-950/80 px-1.5 py-0.5 rounded-none border border-[#263245]">
                                    ${colorPips || '<span class="text-[9px] text-slate-500">Colorless</span>'}
                                </div>
                            </div>
                        </div>

                        <div class="p-4 space-y-3">
                            <h4 class="text-sm font-black text-white font-mono uppercase truncate">${escapeHtml(d.deck_name)}</h4>
                            <div class="flex items-center gap-1.5 flex-wrap">
                                <span class="text-[10px] px-2 py-0.5 rounded-none bg-cyan-500/20 text-cyan-400 border border-cyan-500/40 font-bold">${d.power_level ? `${d.power_level.toFixed(1)} Power` : 'Pre-AI'}</span>
                                <span class="text-[10px] px-2 py-0.5 rounded-none bg-slate-800 text-cyan-300 border border-[#263245] font-mono font-bold">${escapeHtml(d.archetype || 'Midrange / Engine')}</span>
                            </div>

                            <div class="grid grid-cols-2 gap-2 bg-[#1B2230]/70 p-2 rounded-none border border-[#263245] font-mono text-xs">
                                <div>
                                    <span class="text-[10px] text-slate-400 uppercase block">TCG Value</span>
                                    <span class="text-emerald-400 font-bold">$${parseFloat(d.total_value || 0).toFixed(2)}</span>
                                </div>
                                <div>
                                    <span class="text-[10px] text-slate-400 uppercase block">Avg CMC</span>
                                    <span class="text-amber-400 font-bold">${parseFloat(d.avg_cmc || 0).toFixed(2)}</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    }

    // 2. Head-to-Head Delta Matrix & Categorized Profiles (When 2 Decks Selected)
    const deltaSection = document.getElementById('compareDeltaMatrixSection');
    const profilesSection = document.getElementById('compareCategorizedProfilesSection');
    const radarSection = document.getElementById('comparePipRadarSection');

    if (decks.length === 2 && data.delta_matrix) {
        if (deltaSection) deltaSection.classList.remove('hidden');
        if (profilesSection) profilesSection.classList.remove('hidden');
        if (radarSection) radarSection.classList.remove('hidden');

        safeSetText('deltaThDeckA', decks[0].deck_name);
        safeSetText('deltaThDeckB', decks[1].deck_name);
        safeSetText('matchupArchetypeSummary', `${decks[0].archetype || 'Midrange'} vs. ${decks[1].archetype || 'Midrange'}`);

        const matrix = data.delta_matrix;
        const deltaRows = [
            { key: 'nonland_amv', tooltipKey: 'amv', name: 'Nonland Avg Mana Value (AMV)', unit: 'CMC', lowerIsBetter: true },
            { key: 'turn_3_land_pct', tooltipKey: 'land_drops', name: 'Turn 3 Land Drop % (Hypergeometric)', unit: '%', lowerIsBetter: false },
            { key: 'keepability_rate', tooltipKey: 'opening_hand', name: 'Opening Hand Keepability Rate', unit: '%', lowerIsBetter: false },
            { key: 'commander_cast_turn', tooltipKey: 'commander_cast', name: 'Median Commander Cast Turn', unit: 'Turn', lowerIsBetter: true },
            { key: 'fast_ramp', tooltipKey: 'fast_ramp', name: 'Fast Ramp Density (CMC <= 2)', unit: 'cards', lowerIsBetter: false },
            { key: 'dork_or_rock_ramp', tooltipKey: 'ramp_density', name: 'Mana Dorks & Rocks', unit: 'cards', lowerIsBetter: false },
            { key: 'land_fetch_ramp', tooltipKey: 'ramp_density', name: 'Land Ramp Spells', unit: 'cards', lowerIsBetter: false },
            { key: 'treasure_ramp', tooltipKey: 'treasure_count', name: 'Treasure Creation Spells', unit: 'cards', lowerIsBetter: false },
            { key: 'instant_speed_ratio', tooltipKey: 'instant_ratio', name: 'Instant-Speed Interaction %', unit: '%', lowerIsBetter: false },
            { key: 'instant_holdout', tooltipKey: 'instant_holdout', name: 'Instant Mana Holdout', unit: 'CMC', lowerIsBetter: true },
            { key: 'tapland_penalty_index', tooltipKey: 'tapland_ratio', name: 'Tapland Tempo Penalty', unit: '%', lowerIsBetter: true },
            { key: 'targeted_removal', tooltipKey: 'targeted_removal', name: 'Targeted Removal Spells', unit: 'cards', lowerIsBetter: false },
            { key: 'board_wipes', tooltipKey: 'board_wipes', name: 'Board Wipe Spells', unit: 'cards', lowerIsBetter: false },
            { key: 'removal_mana_efficiency', tooltipKey: 'removal_efficiency', name: 'Removal Mana Efficiency', unit: 'CMC', lowerIsBetter: true },
            { key: 'draw_engines', tooltipKey: 'draw_engines', name: 'Engine Card Draw Density', unit: 'cards', lowerIsBetter: false },
            { key: 'virtual_card_advantage', tooltipKey: 'virtual_ca', name: 'Virtual Card Advantage (Draw/Impulse/Recursion)', unit: 'cards', lowerIsBetter: false },
            { key: 'mana_sinks', tooltipKey: 'mana_sinks', name: 'Late-Game Mana Sinks', unit: 'outlets', lowerIsBetter: false },
            { key: 'burst_draw', tooltipKey: 'burst_draw', name: 'Burst Card Draw Spells', unit: 'cards', lowerIsBetter: false },
            { key: 'cantrips', tooltipKey: 'cantrips', name: 'Low-CMC Cantrips', unit: 'cards', lowerIsBetter: false },
            { key: 'tutors_general', tooltipKey: 'tutors', name: 'General Tutor Density', unit: 'cards', lowerIsBetter: false },
            { key: 'tutors_land', tooltipKey: 'tutors', name: 'Land / Ramp Tutors', unit: 'cards', lowerIsBetter: false },
            { key: 'portfolio_value', tooltipKey: 'tcg_value', name: 'Total TCG Market Value', unit: '$', lowerIsBetter: false },
        ];

        const tbody = document.getElementById('deltaMatrixTableBody');
        if (tbody) {
            tbody.innerHTML = deltaRows.map(r => {
                const m = matrix[r.key] || { deck_a: 0, deck_b: 0, delta: 0, advantage: 'Tie' };
                const deltaVal = m.delta;
                const formattedA = r.unit === '$' ? `$${(m.deck_a || 0).toFixed(2)}` : `${m.deck_a || 0}${r.unit === '%' ? '%' : ''}`;
                const formattedB = r.unit === '$' ? `$${(m.deck_b || 0).toFixed(2)}` : `${m.deck_b || 0}${r.unit === '%' ? '%' : ''}`;
                
                let deltaBadge = '<span class="text-slate-400 font-bold">0</span>';
                if (deltaVal > 0) {
                    const color = r.lowerIsBetter ? 'text-rose-400' : 'text-emerald-400';
                    deltaBadge = `<span class="${color} font-bold">+${r.unit === '$' ? '$' : ''}${deltaVal}${r.unit === '%' ? '%' : ''}</span>`;
                } else if (deltaVal < 0) {
                    const color = r.lowerIsBetter ? 'text-emerald-400' : 'text-rose-400';
                    deltaBadge = `<span class="${color} font-bold">${r.unit === '$' ? '-$' : ''}${Math.abs(deltaVal)}${r.unit === '%' ? '%' : ''}</span>`;
                }

                let advBadge = '<span class="text-[10px] px-2 py-0.5 rounded-none bg-slate-800 text-slate-400">Neutral</span>';
                if (m.advantage === 'deck_a') {
                    advBadge = `<span class="text-[10px] px-2 py-0.5 rounded-none bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 font-bold">${escapeHtml(decks[0].deck_name)}</span>`;
                } else if (m.advantage === 'deck_b') {
                    advBadge = `<span class="text-[10px] px-2 py-0.5 rounded-none bg-amber-500/20 text-amber-300 border border-amber-500/40 font-bold">${escapeHtml(decks[1].deck_name)}</span>`;
                }

                return `
                    <tr class="hover:bg-[#1B2230]/40 transition cursor-help" data-tooltip-key="${r.tooltipKey || ''}">
                        <td class="py-2.5 px-4 font-semibold text-white flex items-center gap-1.5">
                            <span>${r.name}</span>
                            <span class="text-cyan-400 text-xs">ⓘ</span>
                        </td>
                        <td class="py-2.5 px-4 text-center text-cyan-400 font-bold">${formattedA}</td>
                        <td class="py-2.5 px-4 text-center text-amber-400 font-bold">${formattedB}</td>
                        <td class="py-2.5 px-4 text-center">${deltaBadge}</td>
                        <td class="py-2.5 px-4 text-right">${advBadge}</td>
                    </tr>
                `;
            }).join('');
        }

        // Populate Categorized Profile Cards
        const vp = data.velocity_profile || {};
        const velEl = document.getElementById('compVelocityContent');
        if (velEl) {
            velEl.innerHTML = `
                <div class="flex justify-between cursor-help" data-tooltip-key="fast_ramp"><span class="text-slate-400">Fast Ramp (CMC &le; 2) ⓘ:</span><span class="text-white font-bold">${decks[0].fast_ramp_count || 0} vs ${decks[1].fast_ramp_count || 0}</span></div>
                <div class="flex justify-between cursor-help" data-tooltip-key="ramp_density"><span class="text-slate-400">Dorks & Rocks / Fetch ⓘ:</span><span class="text-slate-300">${decks[0].dork_or_rock_count || 0}/${decks[0].land_fetch_count || 0} vs ${decks[1].dork_or_rock_count || 0}/${decks[1].land_fetch_count || 0}</span></div>
                <div class="flex justify-between cursor-help" data-tooltip-key="treasure_count"><span class="text-slate-400">Treasure Producers ⓘ:</span><span class="text-amber-400 font-bold">${decks[0].treasure_count || 0} vs ${decks[1].treasure_count || 0}</span></div>
                <div class="flex justify-between cursor-help" data-tooltip-key="tapland_ratio"><span class="text-slate-400">Tapland Penalty ⓘ:</span><span class="text-rose-400 font-bold">${decks[0].tapland_penalty_index || 0}% vs ${decks[1].tapland_penalty_index || 0}%</span></div>
                <div class="flex justify-between border-t border-[#263245] pt-1.5 cursor-help" data-tooltip-key="speed_pacing"><span class="text-slate-400">Velocity Lead:</span><span class="text-emerald-400 font-bold">${vp.velocity_leader === 'deck_a' ? escapeHtml(decks[0].deck_name) : (vp.velocity_leader === 'deck_b' ? escapeHtml(decks[1].deck_name) : 'Tied')}</span></div>
            `;
        }

        const ip = data.interaction_profile || {};
        const intEl = document.getElementById('compInteractionContent');
        if (intEl) {
            intEl.innerHTML = `
                <div class="flex justify-between cursor-help" data-tooltip-key="targeted_removal"><span class="text-slate-400">Targeted Removal ⓘ:</span><span class="text-white font-bold">${decks[0].targeted_removal_count || 0} vs ${decks[1].targeted_removal_count || 0}</span></div>
                <div class="flex justify-between cursor-help" data-tooltip-key="board_wipes"><span class="text-slate-400">Board Wipes ⓘ:</span><span class="text-slate-300">${decks[0].board_wipe_count || 0} vs ${decks[1].board_wipe_count || 0}</span></div>
                <div class="flex justify-between cursor-help" data-tooltip-key="removal_efficiency"><span class="text-slate-400">Removal Efficiency ⓘ:</span><span class="text-slate-300">${parseFloat(decks[0].removal_mana_efficiency || 0).toFixed(2)} vs ${parseFloat(decks[1].removal_mana_efficiency || 0).toFixed(2)} CMC</span></div>
                <div class="flex justify-between cursor-help" data-tooltip-key="instant_ratio"><span class="text-slate-400">Instant Speed % ⓘ:</span><span class="text-cyan-400 font-bold">${decks[0].instant_speed_ratio || 0}% vs ${decks[1].instant_speed_ratio || 0}%</span></div>
                <div class="flex justify-between border-t border-[#263245] pt-1.5 cursor-help" data-tooltip-key="interaction_defense"><span class="text-slate-400">Efficiency Lead:</span><span class="text-cyan-400 font-bold">${ip.interaction_leader === 'deck_a' ? escapeHtml(decks[0].deck_name) : (ip.interaction_leader === 'deck_b' ? escapeHtml(decks[1].deck_name) : 'Tied')}</span></div>
            `;
        }

        const cap = data.card_advantage_profile || {};
        const caEl = document.getElementById('compCardAdvantageContent');
        if (caEl) {
            caEl.innerHTML = `
                <div class="flex justify-between cursor-help" data-tooltip-key="draw_engines"><span class="text-slate-400">Engine Draw ⓘ:</span><span class="text-white font-bold">${decks[0].draw_engine_count || 0} vs ${decks[1].draw_engine_count || 0}</span></div>
                <div class="flex justify-between cursor-help" data-tooltip-key="burst_draw"><span class="text-slate-400">Burst / Cantrips ⓘ:</span><span class="text-slate-300">${decks[0].draw_burst_count || 0}/${decks[0].draw_cantrip_count || 0} vs ${decks[1].draw_burst_count || 0}/${decks[1].draw_cantrip_count || 0}</span></div>
                <div class="flex justify-between cursor-help" data-tooltip-key="tutors"><span class="text-slate-400">General / Land Tutors ⓘ:</span><span class="text-amber-400 font-bold">${decks[0].tutor_general_count || 0}/${decks[0].tutor_land_count || 0} vs ${decks[1].tutor_general_count || 0}/${decks[1].tutor_land_count || 0}</span></div>
                <div class="flex justify-between border-t border-[#263245] pt-1.5 cursor-help" data-tooltip-key="card_advantage"><span class="text-slate-400">Advantage Lead:</span><span class="text-purple-400 font-bold">${cap.advantage_leader === 'deck_a' ? escapeHtml(decks[0].deck_name) : (cap.advantage_leader === 'deck_b' ? escapeHtml(decks[1].deck_name) : 'Tied')}</span></div>
            `;
        }

        renderComparePipRadar(decks);
    } else {
        if (deltaSection) deltaSection.classList.add('hidden');
        if (profilesSection) profilesSection.classList.add('hidden');
        if (radarSection) radarSection.classList.add('hidden');
    }

    // 3. Side-by-Side Mana Curves
    const curveContainer = document.getElementById('compareCurveChartContainer');
    if (curveContainer) {
        curveContainer.className = `grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-${Math.min(decks.length, 4)} gap-4 pt-2`;
        const cmcKeys = ["0", "1", "2", "3", "4", "5", "6", "7+"];

        curveContainer.innerHTML = decks.map(d => {
            const curve = d.cmc_curve || {};
            const maxVal = Math.max(1, ...Object.values(curve));
            const barsHtml = cmcKeys.map(k => {
                const count = curve[k] || 0;
                const heightPct = Math.max(8, Math.round((count / maxVal) * 100));
                return `
                    <div class="flex-1 flex flex-col items-center h-full justify-end group">
                        <span class="text-[9px] font-mono text-cyan-400 font-bold mb-1 opacity-0 group-hover:opacity-100 transition">${count}</span>
                        <div class="w-full bg-gradient-to-t from-cyan-600 to-cyan-400 rounded-t transition-all duration-300" style="height: ${heightPct}%;"></div>
                    </div>
                `;
            }).join('');

            return `
                <div class="bg-[#1B2230]/70 border border-[#263245] p-3.5 rounded-none space-y-2">
                    <div class="flex items-center justify-between text-xs font-mono">
                        <span class="font-bold text-white truncate max-w-[140px]">${escapeHtml(d.deck_name)}</span>
                        <span class="text-amber-400 font-bold">${parseFloat(d.avg_cmc || 0).toFixed(2)} CMC</span>
                    </div>
                    <div class="h-32 flex items-end justify-between gap-1.5 pt-4 pb-1 border-b border-[#263245]">
                        ${barsHtml}
                    </div>
                    <div class="flex justify-between text-[9px] font-mono text-slate-500">
                        <span>0</span><span>1</span><span>2</span><span>3</span><span>4</span><span>5</span><span>6</span><span>7+</span>
                    </div>
                </div>
            `;
        }).join('');
    }

    // 4. Card Type Composition Breakdown
    const compGrid = document.getElementById('compareTypeCompositionGrid');
    if (compGrid) {
        compGrid.className = `grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-${Math.min(decks.length, 4)} gap-4 pt-2`;
        compGrid.innerHTML = decks.map(d => {
            const types = d.type_counts || {};
            const total = d.total_cards || 100;
            const typeKeys = ['Creatures', 'Instants', 'Sorceries', 'Artifacts', 'Enchantments', 'Planeswalkers', 'Lands'];
            
            const typeRows = typeKeys.map(tKey => {
                const count = types[tKey] || 0;
                const pct = Math.round((count / total) * 100);
                return `
                    <div>
                        <div class="flex justify-between text-[11px] font-mono mb-0.5">
                            <span class="text-slate-400">${tKey}</span>
                            <span class="text-white font-bold">${count} <span class="text-slate-500 font-normal">(${pct}%)</span></span>
                        </div>
                        <div class="w-full h-1 bg-slate-800 rounded-full overflow-hidden">
                            <div class="h-full bg-cyan-500 rounded-full" style="width: ${pct}%;"></div>
                        </div>
                    </div>
                `;
            }).join('');

            return `
                <div class="bg-[#1B2230]/70 border border-[#263245] p-3.5 rounded-none space-y-2.5">
                    <div class="flex items-center justify-between text-xs font-mono border-b border-[#263245] pb-1.5">
                        <span class="font-bold text-white truncate max-w-[140px]">${escapeHtml(d.deck_name)}</span>
                        <span class="text-cyan-400 font-bold">${d.land_count || (types['Lands'] || 0)}L / ${d.nonland_count || (total - (types['Lands'] || 0))}S</span>
                    </div>
                    <div class="space-y-1.5">
                        ${typeRows}
                    </div>
                </div>
            `;
        }).join('');
    }

    // 5. Shared Cards Table
    const sharedAllList = data.shared_all || [];
    safeSetText('sharedOverlapSummaryBadge', `${sharedAllList.length} Universal Staples`);
    const sharedTableBody = document.getElementById('sharedAllTableBody');

    if (sharedTableBody) {
        if (sharedAllList.length === 0) {
            sharedTableBody.innerHTML = `<tr><td colspan="6" class="py-6 text-center text-slate-500 font-mono">No cards are shared across all ${decks.length} selected decks.</td></tr>`;
        } else {
            sharedTableBody.innerHTML = sharedAllList.map(c => {
                const price = c.price_usd ? `$${parseFloat(c.price_usd).toFixed(2)}` : '--';
                const img = c.image_uri || '';
                return `
                    <tr class="hover:bg-[#1B2230]/50 transition">
                        <td class="py-2.5 px-3 font-semibold text-white">
                            <span class="cursor-pointer hover:text-cyan-300 hover:underline" data-img="${escapeHtml(img)}"
                                  onmouseenter="showCardHover(event, this)" onmousemove="moveCardHover(event)" onmouseleave="hideCardHover()">
                                ${escapeHtml(c.name)}
                            </span>
                        </td>
                        <td class="py-2.5 px-3 text-slate-400 text-[11px] truncate max-w-[160px]">${escapeHtml(c.type_line || 'Card')}</td>
                        <td class="py-2.5 px-3 text-slate-300 font-sans text-xs">${escapeHtml(c.mana_cost || '')}</td>
                        <td class="py-2.5 px-3 text-center text-amber-400 font-bold">${c.cmc || 0}</td>
                        <td class="py-2.5 px-3 text-right font-bold text-emerald-400">${price}</td>
                        <td class="py-2.5 px-3 text-center">
                            <button onclick="addSharedCardToWatchlist('${escapeHtml(c.name)}', this)" class="px-2 py-0.5 bg-[#1B2230] hover:bg-cyan-500 hover:text-slate-950 text-cyan-400 rounded-none text-[10px] font-mono uppercase font-bold border border-[#263245] transition cursor-pointer">
                                + Watch
                            </button>
                        </td>
                    </tr>
                `;
            }).join('');
        }
    }

    // 6. Unique Signatures Grid
    const uniqueGrid = document.getElementById('compareUniqueSignaturesGrid');
    if (uniqueGrid) {
        uniqueGrid.className = `grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-${Math.min(decks.length, 4)} gap-3`;
        const uniqueMap = data.unique_per_deck || {};

        uniqueGrid.innerHTML = decks.map(d => {
            const uData = uniqueMap[String(d.id)] || { count: 0, sample: [] };
            const sampleCardsHtml = (uData.sample || []).map(c => `
                <li class="text-[11px] text-slate-300 truncate cursor-pointer hover:text-cyan-300" data-img="${escapeHtml(c.image_uri || '')}"
                    onmouseenter="showCardHover(event, this)" onmousemove="moveCardHover(event)" onmouseleave="hideCardHover()">
                    &bull; ${escapeHtml(c.name)}
                </li>
            `).join('');

            return `
                <div class="bg-[#1B2230]/70 border border-[#263245] p-3 rounded-none space-y-2">
                    <div class="flex items-center justify-between border-b border-[#263245] pb-1 text-xs font-mono">
                        <span class="font-bold text-white truncate max-w-[140px]">${escapeHtml(d.deck_name)}</span>
                        <span class="text-amber-400 font-bold">${uData.count} Unique</span>
                    </div>
                    <ul class="space-y-1 pt-0.5">
                        ${sampleCardsHtml || '<li class="text-[11px] text-slate-500">None</li>'}
                    </ul>
                </div>
            `;
        }).join('');
    }

    // 7. Top 5 Most Valuable Cards Grid
    const topCardsGrid = document.getElementById('compareTopCardsGrid');
    if (topCardsGrid) {
        topCardsGrid.className = `grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-${Math.min(decks.length, 4)} gap-4 pt-1`;
        topCardsGrid.innerHTML = decks.map(d => {
            const cardsList = d.top_cards || [];
            const itemsHtml = cardsList.map((c, idx) => `
                <div class="flex items-center justify-between py-1.5 border-b border-[#263245] text-xs font-mono">
                    <div class="min-w-0 pr-2">
                        <span class="cursor-pointer hover:text-cyan-300 font-medium text-slate-200 truncate block" data-img="${escapeHtml(c.image_uri || '')}"
                              onmouseenter="showCardHover(event, this)" onmousemove="moveCardHover(event)" onmouseleave="hideCardHover()">
                            ${idx + 1}. ${escapeHtml(c.name)}
                        </span>
                    </div>
                    <span class="font-bold text-emerald-400 flex-shrink-0">$${parseFloat(c.price || 0).toFixed(2)}</span>
                </div>
            `).join('');

            return `
                <div class="bg-[#1B2230]/70 border border-[#263245] p-3.5 rounded-none space-y-2">
                    <div class="text-xs font-mono font-bold text-white truncate border-b border-[#263245] pb-1.5">${escapeHtml(d.deck_name)}</div>
                    <div>${itemsHtml || '<div class="text-xs text-slate-500 font-mono py-2">No pricing available</div>'}</div>
                </div>
            `;
        }).join('');
    }

    // 8. Strategy & Win-Con Comparison
    const strategyGrid = document.getElementById('compareStrategyGrid');
    if (strategyGrid) {
        strategyGrid.className = `grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-${Math.min(decks.length, 4)} gap-4 pt-1`;
        strategyGrid.innerHTML = decks.map(d => {
            const winCons = d.win_conditions || [];
            const winConsHtml = winCons.map(wc => `
                <div class="bg-slate-900/60 p-2 rounded-none border border-[#263245] text-[11px] space-y-0.5">
                    <div class="font-bold text-amber-300">${escapeHtml(wc.title || 'Win Condition')}</div>
                    <div class="text-slate-400 font-sans text-[10px]">${escapeHtml(wc.description || '')}</div>
                </div>
            `).join('');

            return `
                <div class="bg-[#1B2230]/70 border border-[#263245] p-3.5 rounded-none space-y-2.5">
                    <div class="text-xs font-mono font-bold text-white truncate border-b border-[#263245] pb-1.5">${escapeHtml(d.deck_name)}</div>
                    <p class="text-xs text-slate-300 font-sans leading-relaxed">${escapeHtml(d.summary || 'Strategic overview ready upon AI analysis.')}</p>
                    <div class="space-y-1.5 pt-1">
                        <span class="text-[10px] font-mono text-slate-400 uppercase font-bold block">Primary Win-Cons</span>
                        ${winConsHtml || '<div class="text-[11px] text-slate-500 font-mono">Run Gemini AI to reveal win-cons.</div>'}
                    </div>
                </div>
            `;
        }).join('');
    }
}

// Comparative Radar Chart for Pip Demand Overlay
function renderComparePipRadar(decks) {
    const canvas = document.getElementById('comparePipRadarChart');
    if (!canvas || typeof Chart === 'undefined') return;
    const ctx = canvas.getContext('2d');
    if (comparePipRadarChartInstance) {
        comparePipRadarChartInstance.destroy();
    }

    const labels = ['White (W)', 'Blue (U)', 'Black (B)', 'Red (R)', 'Green (G)', 'Colorless (C)'];
    const keys = ['W', 'U', 'B', 'R', 'G', 'C'];

    const deckA = decks[0] || {};
    const deckB = decks[1] || {};
    const pbA = deckA.pip_breakdown || {};
    const pbB = deckB.pip_breakdown || {};

    const dataA = keys.map(k => (pbA[k] ? pbA[k].pip_demand_pct : 0));
    const dataB = keys.map(k => (pbB[k] ? pbB[k].pip_demand_pct : 0));

    comparePipRadarChartInstance = new Chart(ctx, {
        type: 'radar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: `${deckA.deck_name || 'Deck A'} (% Pip Demand)`,
                    data: dataA,
                    backgroundColor: 'rgba(6, 182, 212, 0.25)',
                    borderColor: '#06b6d4',
                    borderWidth: 2,
                    pointBackgroundColor: '#06b6d4',
                },
                {
                    label: `${deckB.deck_name || 'Deck B'} (% Pip Demand)`,
                    data: dataB,
                    backgroundColor: 'rgba(245, 158, 11, 0.25)',
                    borderColor: '#f59e0b',
                    borderWidth: 2,
                    pointBackgroundColor: '#f59e0b',
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                r: {
                    angleLines: { color: 'rgba(51, 65, 85, 0.6)' },
                    grid: { color: 'rgba(51, 65, 85, 0.6)' },
                    pointLabels: {
                        color: '#94a3b8',
                        font: { family: 'monospace', size: 10 }
                    },
                    ticks: {
                        backdropColor: 'transparent',
                        color: '#64748b',
                        font: { size: 9 },
                        showLabelBackdrop: false
                    },
                    suggestedMin: 0,
                    suggestedMax: 40
                }
            },
            plugins: {
                legend: {
                    display: true,
                    labels: {
                        color: '#cbd5e1',
                        font: { family: 'monospace', size: 11 }
                    }
                }
            }
        }
    });
}

// Watchlist Quick Add from Shared Cards
async function addSharedCardToWatchlist(cardName, btn) {
    if (!cardName) return;
    btn.disabled = true;
    const origText = btn.innerText;
    btn.innerText = "...";

    try {
        const resp = await fetch('/api/watchlist/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: cardName,
                is_any_version: true,
                target_price: 1.0,
                tag: 'Commander Staple'
            })
        });
        const data = await resp.json();
        if (resp.ok) {
            showToast(`Added '${cardName}' to Watchlist!`, 'success');
            btn.className = "px-2 py-0.5 bg-emerald-500/20 text-emerald-300 rounded-none text-[10px] font-mono uppercase font-bold border border-emerald-500/40";
            btn.innerText = "✓ Watched";
        } else if (resp.status === 409) {
            showToast(`'${cardName}' is already on your Watchlist.`, 'info');
            btn.className = "px-2 py-0.5 bg-slate-800 text-slate-400 rounded-none text-[10px] font-mono uppercase font-bold border border-[#263245]";
            btn.innerText = "✓ In List";
        } else {
            showToast(data.error || 'Failed to add to watchlist', 'error');
            btn.disabled = false;
            btn.innerText = origText;
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
        btn.disabled = false;
        btn.innerText = origText;
    }
}

// Toast aliases for compatibility
function showOverviewToast(msg, type = 'info') {
    showToast(msg, type);
}
function hideOverviewToast() {
    hideToast();
}

// Hover Zoom Image Tooltip with automatic Scryfall resolution
function showCardHover(e, elem) {
    let imgUrl = elem.getAttribute('data-img');
    if (!imgUrl && elem.getAttribute('data-card-name')) {
        imgUrl = resolveCardImageUri(elem.getAttribute('data-card-name'));
    }
    if (!imgUrl) return;
    const preview = document.getElementById('cardHoverPreview');
    const img = document.getElementById('cardHoverImg');
    if (img) img.src = imgUrl;
    if (preview) preview.classList.remove('hidden');
    moveCardHover(e);
}
function moveCardHover(e) {
    const preview = document.getElementById('cardHoverPreview');
    if (!preview) return;
    if (window.innerWidth < 768) {
        preview.style.left = '50%';
        preview.style.transform = 'translateX(-50%)';
        preview.style.top = '70px';
    } else {
        preview.style.transform = '';
        preview.style.left = `${(e && e.clientX ? e.clientX : 0) + 20}px`;
        preview.style.top = `${Math.min(window.innerHeight - 340, Math.max(10, (e && e.clientY ? e.clientY : 100) - 100))}px`;
    }
}
function hideCardHover() {
    document.getElementById('cardHoverPreview').classList.add('hidden');
}

// Utility: Sample Decks
function loadSampleDeck(key) {
    switchIngestTab('text');
    const cmdrRadio = document.querySelector('input[name="deckFormatChoice"][value="commander"]');
    const pdhRadio = document.querySelector('input[name="deckFormatChoice"][value="pauper_commander"]');

    if (key === 'murmuring') {
        if (pdhRadio) pdhRadio.checked = true;
        document.getElementById('deckTextInput').value = `// Commander\n1 Murmuring Mystic\n\n// Creatures\n1 Archaeomancer\n1 Peregrine Drake\n1 Cloud of Faeries\n1 Mnemonic Wall\n1 Mulldrifter\n1 Delver of Secrets\n1 Spellstutter Sprite\n\n// Instants & Sorceries\n1 Brainstorm\n1 Ponder\n1 Preordain\n1 Counterspell\n1 Snap\n1 Capsize\n1 Frantic Search\n1 Consider\n1 Opt\n\n// Artifacts\n1 Arcane Signet\n1 Mind Stone\n1 Thought Vessel\n1 Bonder's Ornament\n\n// Lands\n1 Command Tower\n1 Lonely Sandbar\n1 Remote Isle\n1 Mystic Sanctuary\n16 Island`;
    } else {
        if (cmdrRadio) cmdrRadio.checked = true;
        if (key === 'urdragon') {
            document.getElementById('deckTextInput').value = `// Commander\n1 The Ur-Dragon\n\n// Creatures\n1 Utvara Hellkite\n1 Scion of the Ur-Dragon\n1 Miirym, Sentinel Wyrm\n1 Dragonlord Dromoka\n1 Goldspan Dragon\n1 Terror of the Peaks\n1 Old Gnawbone\n1 Klauth, Unrivaled Ancient\n1 Scourge of Valkas\n1 Balefire Dragon\n1 Lathliss, Dragon Queen\n1 Scalelord Reckoner\n1 Dragon Tempest\n\n// Artifacts\n1 Sol Ring\n1 Arcane Signet\n1 Chromatic Lantern\n1 Dragon's Hoard\n1 Orb of Dragonkind\n\n// Lands\n1 Command Tower\n1 Haven of the Spirit Dragon\n1 Crucible of the Spirit Dragon\n4 Forest\n4 Mountain\n3 Plains\n3 Island\n3 Swamp`;
        } else if (key === 'atraxa') {
            document.getElementById('deckTextInput').value = `// Commander\n1 Atraxa, Praetors' Voice\n\n// Planeswalkers\n1 Teferi, Master of Time\n1 Vraska, Betrayal's Sting\n1 Narset Transcendent\n1 Tamiyo, Field Researcher\n1 Ugin, the Spirit Dragon\n1 Oko, Thief of Crowns\n\n// Creatures\n1 Deepglow Skate\n1 Evolution Sage\n1 Flux Channeler\n1 Vorinclex, Monstrous Raider\n\n// Artifacts\n1 Sol Ring\n1 Arcane Signet\n1 Astral Cornucopia\n1 Everflowing Chalice\n1 Contagion Engine\n1 Doubling Season\n\n// Lands\n1 Command Tower\n1 Karn's Bastion\n5 Forest\n5 Island\n5 Plains\n5 Swamp`;
        } else if (key === 'edgar') {
            document.getElementById('deckTextInput').value = `// Commander\n1 Edgar Markov\n\n// Creatures\n1 Captivating Vampire\n1 Cordial Vampire\n1 Stromkirk Captain\n1 Vampire Nighthawk\n1 Bloodline Keeper\n1 Champion of Dusk\n1 Knight of the Ebon Legion\n1 Welcoming Vampire\n1 Falkenrath Noble\n1 Sanctum Seeker\n\n// Artifacts & Enchantments\n1 Sol Ring\n1 Arcane Signet\n1 Skullclamp\n1 Shared Animosity\n1 Phyrexian Arena\n1 Black Market Connections\n\n// Lands\n1 Command Tower\n1 Vault of the Archangel\n6 Swamp\n5 Plains\n5 Mountain`;
        }
    }
}

// Clipboard Paste
async function pasteFromClipboard(elemId) {
    try {
        const text = await navigator.clipboard.readText();
        document.getElementById(elemId).value = text;
        showToast("Pasted from clipboard!", "info");
    } catch (e) {
        showToast("Clipboard access denied.", "error");
    }
}

function handleCsvFileSelected(e) {
    const file = e.target.files[0];
    if (!file) return;
    document.getElementById('csvFileName').innerText = file.name;
    const reader = new FileReader();
    reader.onload = (evt) => {
        window.uploadedCsvContent = evt.target.result;
    };
    reader.readAsText(file);
}

// Gemini Key Modal
function openApiKeyModal() { document.getElementById('geminiApiKeyModal').classList.remove('hidden'); }
function closeApiKeyModal() { document.getElementById('geminiApiKeyModal').classList.add('hidden'); }

async function saveApiKeyFromModal() {
    const key = document.getElementById('modalGeminiApiKey').value.trim();
    const modelSelect = document.getElementById('modalGeminiModel');
    const model = modelSelect ? modelSelect.value : 'gemini-3.8-flash';
    const btn = document.getElementById('modalSaveKeyBtn');
    btn.disabled = true;
    btn.innerText = "Verifying...";

    try {
        const resp = await fetch('/api/deck/gemini-key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: key, model })
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error || "Verification failed");
        showToast("Gemini API key verified & saved!", "success");
        document.getElementById('geminiKeyDot').className = "w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_8px_#34d399]";
        closeApiKeyModal();
    } catch (e) {
        showToast(`Key Error: ${e.message}`, "error");
    } finally {
        btn.disabled = false;
        btn.innerText = "Verify & Save";
    }
}

// Toast Alert Helper
let toastTimer = null;

function showToast(msg, type = 'info') {
    const toast = document.getElementById('analyzerToast');
    const toastBox = document.getElementById('analyzerToastBox');
    const toastMsg = document.getElementById('analyzerToastMsg');
    if (!toast || !toastBox || !toastMsg) return;

    if (toastTimer) {
        clearTimeout(toastTimer);
        toastTimer = null;
    }

    toastMsg.innerText = msg;
    if (type === 'error') {
        toastBox.className = "bg-[#10141D] border border-rose-500/50 text-rose-300 px-4 py-3 rounded-none shadow-2xl flex items-start justify-between space-x-3 text-xs font-mono";
    } else if (type === 'success') {
        toastBox.className = "bg-[#10141D] border border-emerald-500/50 text-emerald-300 px-4 py-3 rounded-none shadow-2xl flex items-start justify-between space-x-3 text-xs font-mono";
    } else {
        toastBox.className = "bg-[#10141D] border border-cyan-500/40 text-cyan-300 px-4 py-3 rounded-none shadow-2xl flex items-start justify-between space-x-3 text-xs font-mono";
    }
    toast.classList.remove('hidden', 'translate-y-2');

    // ONLY auto-hide info and success toasts; keep error toasts visible until manually dismissed or replaced
    if (type !== 'error') {
        toastTimer = setTimeout(() => {
            hideToast();
        }, 5000);
    }
}

function hideToast() {
    const toast = document.getElementById('analyzerToast');
    if (toast) {
        toast.classList.add('hidden', 'translate-y-2');
    }
    if (toastTimer) {
        clearTimeout(toastTimer);
        toastTimer = null;
    }
}

function showAiErrorAlert(msg) {
    const container = document.getElementById('aiErrorAlertContainer');
    const body = document.getElementById('aiErrorAlertBody');
    if (container && body) {
        body.innerText = msg;
        container.classList.remove('hidden');
    }
}

function dismissAiErrorAlert() {
    const container = document.getElementById('aiErrorAlertContainer');
    if (container) {
        container.classList.add('hidden');
    }
}

// =========================================================================
// Dual-Tier Upgrade Engine Handlers ("In Your Binder" & "Shopping List")
// =========================================================================
let currentUpgradeSubtab = 'owned';
window.currentShoppingCards = [];
window.currentShoppingBrackets = { budget: [], moderate: [], high_impact: [] };
window.currentUpgradeTheme = '';
window.antiSaltFilterEnabled = false;

function switchUpgradeSubtab(subtab) {
    currentUpgradeSubtab = subtab;
    const subtabs = ['owned', 'shop', 'primers', 'combos'];
    subtabs.forEach(t => {
        const btn = document.getElementById(`subtab-btn-${t}`);
        const content = document.getElementById(`subtab-content-${t}`);
        if (t === subtab) {
            if (btn) {
                btn.classList.remove('text-slate-400', 'border-transparent');
                if (t === 'owned') btn.classList.add('text-emerald-400', 'border-emerald-400', 'bg-emerald-500/10');
                else if (t === 'shop') btn.classList.add('text-cyan-400', 'border-cyan-400', 'bg-cyan-500/10');
                else if (t === 'primers') btn.classList.add('text-amber-400', 'border-amber-400', 'bg-amber-500/10');
                else if (t === 'combos') btn.classList.add('text-purple-400', 'border-purple-400', 'bg-purple-500/10');
            }
            if (content) content.classList.remove('hidden');
        } else {
            if (btn) {
                btn.className = "py-2 sm:py-3 px-1.5 sm:px-4 text-[10px] sm:text-xs font-mono font-bold uppercase tracking-tight sm:tracking-wider text-slate-400 border-b-2 border-transparent hover:text-slate-200 hover:bg-slate-800/40 sm:hover:bg-transparent transition flex flex-col sm:flex-row items-center justify-center gap-1 sm:gap-2 cursor-pointer rounded-none sm:rounded-none flex-1 text-center";
            }
            if (content) content.classList.add('hidden');
        }
    });
}

function handleUpgradeThemeChange(themeSlug) {
    window.currentUpgradeTheme = themeSlug || '';
    if (activeDeck && activeDeck.id) {
        showToast(themeSlug ? `Filtering recommendations by theme: ${themeSlug}` : "Viewing average commander recommendations", "info");
        loadDualTierUpgrades(activeDeck.id);
    }
}

function toggleAntiSaltFilter() {
    window.antiSaltFilterEnabled = !window.antiSaltFilterEnabled;
    const textEl = document.getElementById('antiSaltStateText');
    const btnEl = document.getElementById('antiSaltToggleBtn');
    if (textEl) {
        textEl.innerText = window.antiSaltFilterEnabled ? "ON" : "OFF";
        textEl.className = window.antiSaltFilterEnabled ? "text-emerald-400 font-black" : "text-slate-400";
    }
    if (btnEl) {
        if (window.antiSaltFilterEnabled) {
            btnEl.classList.add('border-emerald-500/50', 'bg-emerald-950/30', 'text-emerald-300');
        } else {
            btnEl.classList.remove('border-emerald-500/50', 'bg-emerald-950/30', 'text-emerald-300');
        }
    }
    if (activeDeck && activeDeck.id) {
        showToast(window.antiSaltFilterEnabled ? "Casual Mode ON: High salt cards excluded." : "Casual Mode OFF: Showing all cards.", "info");
        loadDualTierUpgrades(activeDeck.id);
    }
}

let enrichmentPollTimer = null;
function trackCollectionEnrichment(status) {
    const pill = document.getElementById('collectionEnrichPill');
    const txt = document.getElementById('collectionEnrichText');
    if (!pill || !txt) return;

    if (!status || (!status.is_running && (status.unresolved_count === 0 || status.unresolved_count === undefined))) {
        pill.classList.add('hidden');
        pill.classList.remove('flex');
        if (enrichmentPollTimer) {
            clearInterval(enrichmentPollTimer);
            enrichmentPollTimer = null;
        }
        return;
    }

    pill.classList.remove('hidden');
    pill.classList.add('flex');
    const pct = status.percent !== undefined ? status.percent : 0;
    txt.textContent = status.is_running ? `Enriching Binder: ${pct}%` : `Binder: ${pct}% Enriched`;

    if (!enrichmentPollTimer && status.is_running) {
        enrichmentPollTimer = setInterval(async () => {
            try {
                const res = await fetch('/api/inventory/enrich/status');
                const pollData = await res.json();
                if (pollData && pollData.success) {
                    if (pollData.is_running) {
                        txt.textContent = `Enriching Binder: ${pollData.percent || 0}%`;
                    } else {
                        pill.classList.add('hidden');
                        pill.classList.remove('flex');
                        clearInterval(enrichmentPollTimer);
                        enrichmentPollTimer = null;
                        showToast("Binder collection enrichment completed!", "success");
                    }
                }
            } catch (e) {
                console.warn("Enrichment poll error:", e);
            }
        }, 4000);
    }
}

function renderUpgradesFromData(data) {
    const promptBanner = document.getElementById('upgradesPromptBanner');
    if (promptBanner) promptBanner.classList.add('hidden');

    safeSetText('ownedSwapsCountBadge', `${data.owned_count || 0} Swaps`);
    safeSetText('shoppingCountBadge', `${data.shopping_count || 0} Cards`);

    renderOwnedSwaps(data.owned_swaps || []);
    renderShoppingList(data.shopping_list || {}, data.all_shopping_cards || []);

    if (data.combos) {
        renderCombos(data.combos);
    }
    if (data.edhrec) {
        updateEdhrecBadgesAndArticles(data.edhrec);
    }
    if (data.enrichment) {
        trackCollectionEnrichment(data.enrichment);
    }
}

async function loadDualTierUpgrades(deckId, options = {}) {
    const { force = false } = options;
    const targetId = deckId || (activeDeck ? activeDeck.id : null);
    if (!targetId) return;

    const promptBanner = document.getElementById('upgradesPromptBanner');
    const loadingElem = document.getElementById('ownedSwapsLoading');
    const emptyElem = document.getElementById('ownedSwapsEmpty');
    const ownedSwapsGrid = document.getElementById('ownedSwapsGrid');

    // 1. If not forcing evaluation, check if stored upgrades exist on activeDeck
    if (!force && !window.currentUpgradeTheme && !window.antiSaltFilterEnabled) {
        if (activeDeck && activeDeck.id === targetId && activeDeck.has_upgrades && activeDeck.upgrades && activeDeck.upgrades.owned_swaps) {
            if (promptBanner) promptBanner.classList.add('hidden');
            if (loadingElem) loadingElem.classList.add('hidden');
            renderUpgradesFromData(activeDeck.upgrades);
            return;
        }

        // 2. If deck has never been evaluated and force is not set, prompt user
        if (activeDeck && activeDeck.id === targetId && !activeDeck.has_upgrades) {
            if (loadingElem) loadingElem.classList.add('hidden');
            if (emptyElem) emptyElem.classList.add('hidden');
            if (ownedSwapsGrid) ownedSwapsGrid.innerHTML = '';
            if (promptBanner) promptBanner.classList.remove('hidden');
            safeSetText('ownedSwapsCountBadge', '0 Swaps');
            safeSetText('shoppingCountBadge', '0 Cards');
            return;
        }
    }

    if (promptBanner) promptBanner.classList.add('hidden');

    const btn = document.getElementById('refreshUpgradesBtn');
    const icon = document.getElementById('refreshUpgradesIcon');
    const text = document.getElementById('refreshUpgradesText');
    if (btn) btn.disabled = true;
    if (icon) icon.classList.add('animate-spin');
    if (text) text.textContent = 'Evaluating...';

    if (loadingElem) loadingElem.classList.remove('hidden');
    if (emptyElem) emptyElem.classList.add('hidden');

    try {
        let url = `/api/deck/${targetId}/upgrades`;
        const params = [];
        if (force) params.push(`refresh=1`);
        if (window.currentUpgradeTheme) params.push(`theme=${encodeURIComponent(window.currentUpgradeTheme)}`);
        if (window.antiSaltFilterEnabled) params.push(`anti_salt=1`);
        if (params.length > 0) url += `?${params.join('&')}`;

        const resp = await fetch(url);
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error || "Failed to load upgrades");

        if (loadingElem) loadingElem.classList.add('hidden');

        // Store back into activeDeck so subsequent tab clicks use memory
        if (activeDeck && activeDeck.id === targetId) {
            activeDeck.has_upgrades = true;
            activeDeck.upgrades = data;
        }

        renderUpgradesFromData(data);

        const ownedCount = data.owned_count || 0;
        const shopCount = data.shopping_count || 0;
        const msg = data.cached
            ? `Displaying stored upgrades: ${ownedCount} binder swaps, ${shopCount} shopping cards`
            : `Evaluation complete: ${ownedCount} binder swaps, ${shopCount} shopping cards`;
        showToast(msg, "success");

    } catch (err) {
        if (loadingElem) loadingElem.classList.add('hidden');
        showToast(`Upgrade evaluation failed: ${err.message}`, "error");
    } finally {
        if (btn) btn.disabled = false;
        if (icon) icon.classList.remove('animate-spin');
        if (text) text.textContent = 'Re-evaluate';
    }
}

function refreshDualTierUpgrades() {
    if (activeDeck && activeDeck.id) {
        loadDualTierUpgrades(activeDeck.id, { force: true });
    }
}

async function loadEdhrecMetadataForDeck(deck) {
    const strip = document.getElementById('edhrecBadgeStrip');
    if (!deck || !deck.id) {
        if (strip) { strip.classList.add('hidden'); strip.classList.remove('flex'); }
        return;
    }

    try {
        let url = `/api/deck/${deck.id}/edhrec`;
        if (window.currentUpgradeTheme) url += `?theme=${encodeURIComponent(window.currentUpgradeTheme)}`;
        const resp = await fetch(url);
        const data = await resp.json();
        if (resp.ok && data.success && data.found && data.edhrec) {
            updateEdhrecBadgesAndArticles(data.edhrec);
            if (data.combos_evaluation) {
                renderCombos(data.combos_evaluation);
            }
        } else {
            if (strip) { strip.classList.add('hidden'); strip.classList.remove('flex'); }
        }
    } catch (e) {
        console.warn("Could not fetch EDHREC data:", e);
        if (strip) { strip.classList.add('hidden'); strip.classList.remove('flex'); }
    }
}

function updateEdhrecBadgesAndArticles(edhrec) {
    const strip = document.getElementById('edhrecBadgeStrip');
    if (!edhrec) {
        if (strip) { strip.classList.add('hidden'); strip.classList.remove('flex'); }
        return;
    }

    if (strip) {
        strip.classList.remove('hidden');
        strip.classList.add('flex');
    }

    const rank = edhrec.rank !== undefined && edhrec.rank !== null ? `#${edhrec.rank} Overall` : 'Unranked';
    safeSetText('edhrecRankBadge', `Rank: ${rank}`);

    const decks = (edhrec.num_decks || 0).toLocaleString();
    safeSetText('edhrecDecksBadge', `Decks: ${decks}`);

    const salt = edhrec.salt_score !== null && edhrec.salt_score !== undefined ? edhrec.salt_score : '--';
    safeSetText('edhrecSaltBadge', `Salt: ${salt}`);

    const linkBadge = document.getElementById('edhrecLinkBadge');
    if (linkBadge && edhrec.edhrec_url) {
        linkBadge.href = edhrec.edhrec_url;
    }

    // Populate theme dropdown
    const themeSel = document.getElementById('upgradeThemeSelect');
    if (themeSel && edhrec.themes && Array.isArray(edhrec.themes)) {
        const currentVal = window.currentUpgradeTheme || '';
        let optionsHtml = '<option value="">🎯 All Themes (Average)</option>';
        edhrec.themes.forEach(t => {
            const isSel = (t.slug === currentVal) ? 'selected' : '';
            optionsHtml += `<option value="${escapeHtml(t.slug)}" ${isSel}>${escapeHtml(t.name)} (${(t.count || 0).toLocaleString()})</option>`;
        });
        themeSel.innerHTML = optionsHtml;
    }

    // Render articles
    renderEdhrecArticles(edhrec.articles || []);
}

function renderEdhrecArticles(articles) {
    const grid = document.getElementById('edhrecArticlesGrid');
    const empty = document.getElementById('edhrecArticlesEmpty');
    safeSetText('articlesCountBadge', (articles || []).length);

    if (!grid) return;

    if (!articles || articles.length === 0) {
        grid.innerHTML = '';
        if (empty) empty.classList.remove('hidden');
        return;
    }

    if (empty) empty.classList.add('hidden');

    grid.innerHTML = articles.map(art => `
        <div class="bg-[#1B2230]/90 border border-[#263245] rounded-none overflow-hidden shadow-lg flex flex-col justify-between hover:border-cyan-500/50 transition group">
            <div>
                ${art.media ? `
                    <div class="h-36 w-full overflow-hidden bg-slate-900 relative">
                        <img src="${escapeHtml(art.media)}" alt="${escapeHtml(art.title)}" loading="lazy" decoding="async" class="w-full h-full object-cover group-hover:scale-105 transition duration-300">
                    </div>
                ` : ''}
                <div class="p-4 space-y-2">
                    <div class="flex items-center justify-between text-[10px] font-mono text-slate-400">
                        <span>${escapeHtml(art.author || 'EDHREC Contributor')}</span>
                        <span>${escapeHtml(art.date || '')}</span>
                    </div>
                    <h5 class="font-bold text-white text-sm group-hover:text-cyan-300 transition line-clamp-2">
                        ${escapeHtml(art.title)}
                    </h5>
                    ${art.excerpt ? `
                        <p class="text-xs text-slate-400 font-sans line-clamp-3 leading-relaxed">
                            ${escapeHtml(art.excerpt)}
                        </p>
                    ` : ''}
                </div>
            </div>
            <div class="p-4 pt-0">
                <a href="${escapeHtml(art.url)}" target="_blank" rel="noopener noreferrer" 
                   class="w-full py-2 px-3 rounded-none bg-slate-800 hover:bg-cyan-500 hover:text-slate-950 text-cyan-400 text-xs font-mono font-bold uppercase transition flex items-center justify-center gap-1.5">
                    <span>Read Primer on EDHREC</span>
                    <span>↗</span>
                </a>
            </div>
        </div>
    `).join('');
}

function renderCombos(combosEval) {
    const active = (combosEval && combosEval.active) || [];
    const near = (combosEval && combosEval.near) || [];
    const total = active.length + near.length;

    safeSetText('combosCountBadge', total);
    safeSetText('activeCombosCountBadge', active.length);
    safeSetText('nearCombosCountBadge', near.length);

    const activeGrid = document.getElementById('activeCombosGrid');
    const activeEmpty = document.getElementById('activeCombosEmpty');
    const nearGrid = document.getElementById('nearCombosGrid');
    const nearEmpty = document.getElementById('nearCombosEmpty');
    const emptyAll = document.getElementById('combosEmpty');

    if (total === 0) {
        if (emptyAll) emptyAll.classList.remove('hidden');
        if (activeGrid) activeGrid.innerHTML = '';
        if (nearGrid) nearGrid.innerHTML = '';
        return;
    }
    if (emptyAll) emptyAll.classList.add('hidden');

    if (activeGrid) {
        if (active.length === 0) {
            activeGrid.innerHTML = '';
            if (activeEmpty) activeEmpty.classList.remove('hidden');
        } else {
            if (activeEmpty) activeEmpty.classList.add('hidden');
            activeGrid.innerHTML = active.map(c => `
                <div class="bg-[#1B2230]/90 border border-emerald-500/40 rounded-none p-4 space-y-2 shadow-lg">
                    <div class="flex items-start justify-between gap-2">
                        <span class="text-[10px] px-2 py-0.5 rounded-none bg-emerald-500/20 text-emerald-300 font-mono font-bold uppercase border border-emerald-500/40">
                            Complete Combo Loop
                        </span>
                        ${c.url ? `
                            <a href="${escapeHtml(c.url)}" target="_blank" rel="noopener noreferrer" class="text-xs text-cyan-400 hover:underline font-mono">
                                Spellbook ↗
                            </a>
                        ` : ''}
                    </div>
                    <div class="font-bold text-white text-xs">${escapeHtml(c.name)}</div>
                    <div class="flex flex-wrap gap-1 pt-1">
                        ${(c.pieces || []).map(p => `
                            <span class="text-[10px] px-1.5 py-0.5 rounded-none bg-emerald-950/40 border border-emerald-500/30 text-emerald-300 font-mono cursor-pointer hover:border-emerald-400"
                                  data-img="${escapeHtml(resolveCardImageUri(p))}"
                                  data-card-name="${escapeHtml(p)}"
                                  onmouseenter="showCardHover(event, this)"
                                  onmousemove="moveCardHover(event)"
                                  onmouseleave="hideCardHover()"
                                  onclick="showCardHover(event, this)">
                                ✔ ${escapeHtml(p)}
                            </span>
                        `).join('')}
                    </div>
                </div>
            `).join('');
        }
    }

    if (nearGrid) {
        if (near.length === 0) {
            nearGrid.innerHTML = '';
            if (nearEmpty) nearEmpty.classList.remove('hidden');
        } else {
            if (nearEmpty) nearEmpty.classList.add('hidden');
            nearGrid.innerHTML = near.map(c => `
                <div class="bg-[#1B2230]/90 border border-amber-500/40 rounded-none p-4 space-y-2 shadow-lg">
                    <div class="flex items-start justify-between gap-2">
                        <span class="text-[10px] px-2 py-0.5 rounded-none bg-amber-500/20 text-amber-300 font-mono font-bold uppercase border border-amber-500/40">
                            Missing ${c.missing_count || 1} Piece${c.missing_count > 1 ? 's' : ''}
                        </span>
                        ${c.url ? `
                            <a href="${escapeHtml(c.url)}" target="_blank" rel="noopener noreferrer" class="text-xs text-cyan-400 hover:underline font-mono">
                                Spellbook ↗
                            </a>
                        ` : ''}
                    </div>
                    <div class="font-bold text-white text-xs">${escapeHtml(c.name)}</div>
                    <div class="flex flex-wrap gap-1.5 pt-1 text-[10px] font-mono">
                        ${(c.in_deck_pieces || []).map(p => `
                            <span class="px-1.5 py-0.5 rounded-none bg-emerald-950/40 border border-emerald-500/30 text-emerald-300 cursor-pointer hover:border-emerald-400"
                                  data-img="${escapeHtml(resolveCardImageUri(p))}"
                                  data-card-name="${escapeHtml(p)}"
                                  onmouseenter="showCardHover(event, this)"
                                  onmousemove="moveCardHover(event)"
                                  onmouseleave="hideCardHover()"
                                  onclick="showCardHover(event, this)">
                                ✔ In Deck: ${escapeHtml(p)}
                            </span>
                        `).join('')}
                        ${(c.missing_pieces || []).map(m => `
                            <span class="px-1.5 py-0.5 rounded-none ${m.in_binder ? 'bg-purple-950/40 border border-purple-500/40 text-purple-300 font-bold' : 'bg-rose-950/40 border border-rose-500/40 text-rose-300'} cursor-pointer hover:border-white"
                                  data-img="${escapeHtml(resolveCardImageUri(m.name))}"
                                  data-card-name="${escapeHtml(m.name)}"
                                  onmouseenter="showCardHover(event, this)"
                                  onmousemove="moveCardHover(event)"
                                  onmouseleave="hideCardHover()"
                                  onclick="showCardHover(event, this)">
                                ✖ Missing: ${escapeHtml(m.name)} ${m.in_binder ? '(🎴 In Binder)' : ''}
                            </span>
                        `).join('')}
                    </div>

                    <!-- 1-Click Action Footer for Missing Pieces -->
                    <div class="flex flex-wrap items-center gap-2 pt-2 border-t border-[#263245]">
                        ${(c.missing_pieces || []).map(m => `
                            <div class="flex items-center gap-1.5 bg-[#0B0F17] p-1.5 rounded-none border border-[#263245]">
                                <span class="text-[10px] font-mono text-slate-300 font-bold truncate max-w-[120px]">${escapeHtml(m.name)}:</span>
                                <button type="button" onclick="handleSlotComboPiece(this)"
                                        data-card-in="${escapeHtml(m.name)}"
                                        data-card-out="${escapeHtml(m.suggested_cut || c.suggested_cut || '')}"
                                        class="px-2 py-0.5 rounded-none text-[9px] font-mono font-bold uppercase transition flex items-center gap-1 cursor-pointer ${m.in_binder ? 'bg-purple-600 hover:bg-purple-500 text-white shadow-sm' : 'bg-cyan-600 hover:bg-cyan-500 text-slate-950'}">
                                    <span>⚡ ${m.in_binder ? 'Slot (Binder)' : (m.suggested_cut || c.suggested_cut ? `Swap for ${escapeHtml(m.suggested_cut || c.suggested_cut)}` : '+ Add')}</span>
                                </button>
                                <button type="button" onclick="handleUpgradeWatchClick(this)"
                                        data-name="${escapeHtml(m.name)}"
                                        class="px-2 py-0.5 rounded-none text-[9px] font-mono font-bold uppercase transition bg-[#1B2230] hover:bg-cyan-500 hover:text-slate-950 text-cyan-400 border border-[#263245] flex items-center gap-1 cursor-pointer">
                                    <span>+ Watch</span>
                                </button>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `).join('');
        }
    }
}

// 1-Click Action Handler for Missing Combo Pieces
async function handleSlotComboPiece(btn) {
    if (!activeDeck || !activeDeck.id) {
        showToast("Select or open a deck in Inspector first", "warning");
        return;
    }
    const cardIn = btn.getAttribute('data-card-in');
    const cardOut = btn.getAttribute('data-card-out');
    if (!cardIn) return;

    btn.disabled = true;
    const oldHtml = btn.innerHTML;
    btn.innerHTML = '<span>Slotting...</span>';

    try {
        let resp;
        if (cardOut) {
            resp = await fetch(`/api/deck/${activeDeck.id}/apply-swap`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ card_in: cardIn, card_out: cardOut })
            });
        } else {
            resp = await fetch(`/api/deck/${activeDeck.id}/add-card`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ card_name: cardIn })
            });
        }
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.error || 'Failed to slot card');
        }
        showToast(data.message || `Slotted ${cardIn} into deck!`, "success");
        if (data.deck) {
            displayDeckInInspector(data.deck, true);
        } else {
            await loadSavedDeckIntoInspector(activeDeck.id, currentInspectorTab);
        }
        await refreshVaultList();
    } catch (err) {
        showToast(err.message || 'Error slotting combo piece', "error");
    } finally {
        btn.disabled = false;
        btn.innerHTML = oldHtml;
    }
}

function renderOwnedSwaps(swaps) {
    const grid = document.getElementById('ownedSwapsGrid');
    const empty = document.getElementById('ownedSwapsEmpty');
    if (!grid) return;

    if (!swaps || swaps.length === 0) {
        grid.innerHTML = '';
        if (empty) empty.classList.remove('hidden');
        return;
    }

    if (empty) empty.classList.add('hidden');

    grid.innerHTML = swaps.map(u => `
        <div class="bg-[#1B2230]/80 p-4 rounded-none border border-[#263245] flex flex-col justify-between space-y-3.5 shadow-lg relative overflow-hidden">
            <div class="flex items-start justify-between gap-2">
                <div class="flex items-center gap-1.5 flex-wrap">
                    <span class="text-[10px] px-1.5 py-0.5 rounded-none bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 uppercase font-bold font-mono">
                        ${escapeHtml(u.category || 'Binder Upgrade')}
                    </span>
                    ${u.synergy_percent !== undefined && u.synergy_percent !== null && u.synergy_percent > 0 ? `
                        <span class="text-[10px] px-1.5 py-0.5 rounded-none bg-emerald-500/25 text-emerald-300 border border-emerald-500/40 font-mono font-bold shadow-sm">
                            +${u.synergy_percent}% Synergy
                        </span>
                    ` : ''}
                    ${u.inclusion_percent ? `
                        <span class="text-[10px] px-1.5 py-0.5 rounded-none bg-sky-500/20 text-sky-300 border border-sky-500/30 font-mono">
                            ${u.inclusion_percent}% Decks
                        </span>
                    ` : ''}
                    ${u.salt_score ? `
                        <span class="text-[10px] px-1.5 py-0.5 rounded-none bg-rose-500/20 text-rose-300 border border-rose-500/30 font-mono">
                            🧂 ${u.salt_score}
                        </span>
                    ` : ''}
                    <span class="text-[10px] text-slate-400 font-mono">
                        Impact: <strong class="text-emerald-400">${escapeHtml(u.estimated_impact || 'High')}</strong>
                    </span>
                </div>
                <div class="text-[10px] font-mono text-emerald-400 font-bold bg-emerald-950/40 px-2 py-0.5 border border-emerald-500/30">
                    ZERO COST
                </div>
            </div>

            <!-- Card Swap In/Out Comparison Matrix -->
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
                <!-- IN: Owned Card -->
                <div class="bg-emerald-950/25 border border-emerald-500/30 p-2.5 rounded-none space-y-1">
                    <span class="text-[10px] text-emerald-400 uppercase font-bold font-mono block">&darr; IN (FROM BINDER)</span>
                    <div class="flex items-center gap-2">
                        ${u.card_in_image ? `
                            <img src="${escapeHtml(toScryfallThumb(u.card_in_image))}" alt="${escapeHtml(u.card_in)}" loading="lazy" decoding="async" class="w-7 h-10 object-cover rounded-none border border-emerald-500/40 flex-shrink-0 cursor-pointer shadow"
                                 data-img="${escapeHtml(u.card_in_image)}"
                                 onclick="showCardHover(event, this)"
                                 onmouseenter="showCardHover(event, this)" onmousemove="moveCardHover(event)" onmouseleave="hideCardHover()">
                        ` : ''}
                        <div class="min-w-0 flex-1">
                            <div class="font-bold text-white truncate hover:text-emerald-300 transition cursor-pointer" title="${escapeHtml(u.card_in)}"
                                 data-img="${escapeHtml(u.card_in_image || '')}"
                                 onclick="showCardHover(event, this)"
                                 onmouseenter="showCardHover(event, this)" onmousemove="moveCardHover(event)" onmouseleave="hideCardHover()">
                                 ${escapeHtml(u.card_in)}
                            </div>
                            <div class="text-[10px] text-slate-400 font-mono flex items-center gap-1.5 flex-wrap pt-0.5">
                                <span class="text-cyan-400 font-bold">${escapeHtml(u.card_in_set || '---')}</span>
                                ${u.card_in_foil && u.card_in_foil !== 'normal' ? `<span class="text-amber-400 font-bold">★ ${escapeHtml(u.card_in_foil)}</span>` : ''}
                                <span>&bull;</span>
                                <span>${escapeHtml(u.card_in_condition || 'NM')}</span>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- OUT: Card to Cut -->
                <div class="bg-rose-950/25 border border-rose-500/30 p-2.5 rounded-none space-y-1">
                    <span class="text-[10px] text-rose-400 uppercase font-bold font-mono block">&uarr; OUT (RECOMMENDED CUT)</span>
                    <div class="font-bold text-slate-300 truncate" title="${escapeHtml(u.card_out)}">
                        ${escapeHtml(u.card_out)}
                    </div>
                    <div class="text-[10px] text-slate-500 font-mono pt-0.5">
                        ${u.card_out_cmc !== null && u.card_out_cmc !== undefined ? `CMC: ${u.card_out_cmc}` : ''}
                        ${u.card_out_type ? `&bull; ${escapeHtml(u.card_out_type)}` : ''}
                    </div>
                </div>
            </div>

            <!-- Allocation Status Banner -->
            <div class="text-[11px] font-mono p-2 rounded-none border ${u.already_allocated ? 'bg-amber-950/20 border-amber-500/30 text-amber-300' : 'bg-emerald-950/20 border-emerald-500/30 text-emerald-300'}">
                ${u.already_allocated 
                    ? `⚠️ <span class="font-bold">Already Allocated</span> in: ${escapeHtml((u.allocated_in || []).join(', ') || 'Another Deck')}`
                    : `✅ <span class="font-bold">Ready to Slot:</span> ${u.available_copies || 1} available copy in binder`
                }
            </div>

            <!-- Tactical Rationale -->
            <p class="text-[11px] text-slate-300 font-sans leading-relaxed">
                ${escapeHtml(u.rationale || '')}
            </p>

            <!-- 1-Click Apply Swap Action -->
            <button type="button" onclick="handleApplySwapClick(this)" data-out="${escapeHtml(u.card_out || '')}" data-in="${escapeHtml(u.card_in || '')}"
                    class="w-full py-2 bg-emerald-500 hover:bg-emerald-600 text-slate-950 font-mono font-bold text-xs uppercase transition flex items-center justify-center gap-2 shadow-md">
                <span>Apply Swap to Deck</span>
                <span class="font-bold">&rarr;</span>
            </button>
        </div>
    `).join('');
}

async function handleApplySwapClick(btn) {
    const cardOut = btn.getAttribute('data-out') || '';
    const cardIn = btn.getAttribute('data-in') || '';
    await applyDeckSwap(btn, cardOut, cardIn);
}

async function applyDeckSwap(btn, cardOut, cardIn) {
    if (!activeDeck || !activeDeck.id) {
        showToast("Please save or open a saved deck to apply swaps.", "error");
        return;
    }

    cardOut = cardOut || (btn ? btn.getAttribute('data-out') : '') || '';
    cardIn = cardIn || (btn ? btn.getAttribute('data-in') : '') || '';

    const origText = btn ? btn.innerHTML : '';
    if (btn) {
        btn.setAttribute('disabled', 'true');
        btn.innerHTML = '<span class="inline-block animate-spin mr-1">⟳</span> Applying Swap...';
    }

    try {
        const resp = await fetch(`/api/deck/${activeDeck.id}/apply-swap`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ card_out: cardOut, card_in: cardIn })
        });

        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.error || "Failed to apply card swap.");
        }

        activeDeck = data.deck;
        displayDeckInInspector(activeDeck, true);

        showToast(data.message || `Successfully swapped ${cardOut} for ${cardIn}!`, "success");
        await loadDualTierUpgrades(activeDeck.id);

    } catch (err) {
        if (btn) {
            btn.removeAttribute('disabled');
            btn.innerHTML = origText;
        }
        showToast(`Swap failed: ${err.message}`, "error");
    }
}

function renderShoppingList(brackets, allCards) {
    window.currentShoppingCards = allCards || [];
    window.currentShoppingBrackets = brackets || { budget: [], moderate: [], high_impact: [] };

    safeSetText('bracketCountAll', (allCards || []).length);
    safeSetText('bracketCountBudget', (brackets.budget || []).length);
    safeSetText('bracketCountModerate', (brackets.moderate || []).length);
    safeSetText('bracketCountHigh', (brackets.high_impact || []).length);

    filterShoppingBracket('all');
}

function filterShoppingBracket(bracket) {
    const btns = ['all', 'budget', 'moderate', 'high_impact'];
    btns.forEach(b => {
        const el = document.getElementById(`btn-bracket-${b}`);
        if (el) {
            if (b === bracket) {
                el.className = "px-3 py-1 text-xs font-mono font-bold rounded-none bg-cyan-500 text-slate-950 transition";
            } else {
                el.className = "px-3 py-1 text-xs font-mono font-bold rounded-none bg-[#1B2230] text-slate-400 hover:text-white border border-[#263245] transition";
            }
        }
    });

    let displayList = [];
    if (bracket === 'all') {
        displayList = window.currentShoppingCards;
    } else {
        displayList = window.currentShoppingBrackets[bracket] || [];
    }

    const grid = document.getElementById('shoppingListGrid');
    if (!grid) return;

    if (!displayList || displayList.length === 0) {
        grid.innerHTML = `
            <div class="col-span-2 py-10 text-center text-slate-400 font-mono text-xs bg-[#10141D] border border-[#263245] rounded-none">
                No acquisitions matching this budget filter.
            </div>
        `;
        return;
    }

    grid.innerHTML = displayList.map(item => `
        <div class="bg-[#1B2230]/80 p-4 rounded-none border border-[#263245] flex flex-col justify-between space-y-3.5 shadow-lg">
            <div class="flex items-start justify-between gap-2">
                <div class="flex items-center gap-1.5 flex-wrap">
                    <span class="text-[10px] px-1.5 py-0.5 rounded-none bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 uppercase font-bold font-mono">
                        ${escapeHtml(item.category || 'Market Acquisition')}
                    </span>
                    ${item.synergy_percent !== undefined && item.synergy_percent !== null && item.synergy_percent > 0 ? `
                        <span class="text-[10px] px-1.5 py-0.5 rounded-none bg-emerald-500/25 text-emerald-300 border border-emerald-500/40 font-mono font-bold shadow-sm">
                            +${item.synergy_percent}% Synergy
                        </span>
                    ` : ''}
                    ${item.inclusion_percent ? `
                        <span class="text-[10px] px-1.5 py-0.5 rounded-none bg-sky-500/20 text-sky-300 border border-sky-500/30 font-mono">
                            ${item.inclusion_percent}% Decks
                        </span>
                    ` : ''}
                    ${item.salt_score ? `
                        <span class="text-[10px] px-1.5 py-0.5 rounded-none bg-rose-500/20 text-rose-300 border border-rose-500/30 font-mono">
                            🧂 ${item.salt_score}
                        </span>
                    ` : ''}
                    <span class="text-[10px] text-slate-400 font-mono">
                        Impact: <strong class="text-cyan-400">${escapeHtml(item.estimated_impact || 'High')}</strong>
                    </span>
                </div>
                <div class="text-right">
                    ${item.price_usd !== null && item.price_usd !== undefined
                        ? `<span class="text-sm font-mono font-black text-emerald-400">$${item.price_usd.toFixed(2)}</span>`
                        : `<span class="text-xs font-mono text-slate-500">Market Price</span>`
                    }
                </div>
            </div>

            <!-- Card Details & Cut Suggestion -->
            <div class="grid grid-cols-2 gap-2 text-xs">
                <div class="bg-cyan-950/20 border border-cyan-500/30 p-2.5 rounded-none space-y-0.5">
                    <span class="text-[10px] text-cyan-400 uppercase font-bold font-mono block">&darr; TARGET ACQUISITION</span>
                    <div class="flex items-center gap-2">
                        ${item.image_uri ? `
                            <img src="${escapeHtml(toScryfallThumb(item.image_uri))}" alt="${escapeHtml(item.name)}" loading="lazy" decoding="async" class="w-7 h-10 object-cover rounded-none border border-cyan-500/40 flex-shrink-0 cursor-pointer shadow"
                                 data-img="${escapeHtml(item.image_uri)}"
                                 onmouseenter="showCardHover(event, this)" onmousemove="moveCardHover(event)" onmouseleave="hideCardHover()">
                        ` : ''}
                        <div class="min-w-0 flex-1">
                            <div class="font-bold text-white truncate cursor-pointer hover:text-cyan-300 transition" title="${escapeHtml(item.name)}"
                                 data-img="${escapeHtml(item.image_uri || '')}"
                                 onmouseenter="showCardHover(event, this)" onmousemove="moveCardHover(event)" onmouseleave="hideCardHover()">
                                ${escapeHtml(item.name)}
                            </div>
                            <div class="text-[10px] text-slate-400 font-mono">
                                ${item.mana_cost ? `<span>${escapeHtml(item.mana_cost)}</span> &bull; ` : ''}
                                <span>${escapeHtml(item.type_line || 'Card')}</span>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="bg-rose-950/20 border border-rose-500/30 p-2.5 rounded-none space-y-0.5">
                    <span class="text-[10px] text-rose-400 uppercase font-bold font-mono block">&uarr; PROPOSED CUT</span>
                    <div class="font-bold text-slate-300 truncate" title="${escapeHtml(item.card_out || 'Suboptimal Card')}">
                        ${escapeHtml(item.card_out || 'Suboptimal Card')}
                    </div>
                    <span class="text-[10px] text-slate-500 font-mono">Replace to optimize</span>
                </div>
            </div>

            <p class="text-[11px] text-slate-300 font-sans leading-relaxed">
                ${escapeHtml(item.rationale || '')}
            </p>


            <div class="flex items-center gap-2 pt-1 flex-wrap">
                <button type="button" onclick="handleApplySwapClick(this)" data-out="${escapeHtml(item.card_out || '')}" data-in="${escapeHtml(item.name || '')}"
                        class="flex-1 py-1.5 bg-emerald-500/20 hover:bg-emerald-500 text-emerald-400 hover:text-slate-950 text-xs font-mono font-bold uppercase rounded-none border border-emerald-500/40 transition text-center shadow flex items-center justify-center gap-1">
                    <span>Slot Card</span>
                    <span>&rarr;</span>
                </button>
                <button type="button" onclick="handleUpgradeWatchClick(this)" data-name="${escapeHtml(item.name || '')}"
                        class="px-3 py-1.5 bg-cyan-500/10 hover:bg-cyan-500 text-cyan-400 hover:text-slate-950 text-xs font-mono font-bold uppercase rounded-none border border-cyan-500/30 transition text-center">
                    + Watchlist
                </button>
                ${item.tcgplayer_url 
                    ? `<a href="${item.tcgplayer_url}" target="_blank" class="px-2.5 py-1.5 bg-[#10141D] hover:bg-[#222B3D] text-slate-300 hover:text-white text-xs font-mono font-bold uppercase rounded-none border border-[#263245] transition">
                        TCG &rarr;
                       </a>`
                    : ''
                }
            </div>
        </div>
    `).join('');
}

async function exportManaBoxWishlist(format) {
    if (!activeDeck || !activeDeck.id) {
        showToast("Open a saved deck to export wishlist.", "error");
        return;
    }

    try {
        const resp = await fetch(`/api/deck/${activeDeck.id}/wishlist/export`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ format: format })
        });

        if (!resp.ok) throw new Error("Failed to generate wishlist export");

        const blob = await resp.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = `${(activeDeck.deck_name || 'Commander_Deck').replace(/[^a-zA-Z0-9_-]/g, '_')}_Wishlist.${format === 'csv' ? 'csv' : 'txt'}`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        showToast(`Exported ManaBox wishlist (${format.toUpperCase()})!`, "success");

    } catch (err) {
        showToast(`Export failed: ${err.message}`, "error");
    }
}

async function copyWishlistText() {
    if (!window.currentShoppingCards || window.currentShoppingCards.length === 0) {
        showToast("Shopping list is empty.", "info");
        return;
    }

    const lines = window.currentShoppingCards.map(c => `1 ${c.name}`);
    const text = lines.join('\n');

    try {
        await navigator.clipboard.writeText(text);
        showToast(`Copied ${lines.length} wishlist cards to clipboard!`, "success");
    } catch (e) {
        showToast("Clipboard copy failed.", "error");
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

function escapeJs(str) {
    if (!str) return '';
    return String(str).replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '\\"');
}


// =========================================================================
// COMMANDER TACTICS // SITUATIONAL GUIDANCE MODULE
// =========================================================================

let currentDeckTactics = null;
let currentTacticsStatus = null;

const TACTICS_META = {
    'opening_hand': { title: 'Opening Hand & Mulligan Guide', icon: '✋' },
    'game_plan': { title: 'Game Plan by Phase', icon: '🗺️' },
    'combos': { title: 'Combos & Synergy Lines', icon: '⚡' },
    'threats': { title: 'Threats & Interaction', icon: '🎯' },
    'commander_usage': { title: 'Commander Usage & Timing', icon: '👑' }
};

function getTacticsTitle(sec) {
    return (TACTICS_META[sec] && TACTICS_META[sec].title) || sec;
}

function getTacticsIcon(sec) {
    return (TACTICS_META[sec] && TACTICS_META[sec].icon) || '⚡';
}

function updateTacticsBadgeAndBanner(deck) {
    const badge = document.getElementById('tacticsTabBadge');
    const staleBanner = document.getElementById('tacticsStaleBanner');
    const deckBadge = document.getElementById('tacticsActiveDeckBadge');
    
    if (deckBadge) {
        deckBadge.innerText = deck ? (deck.deck_name || 'Active Deck') : '';
    }

    if (!deck) {
        if (badge) badge.classList.add('hidden');
        if (staleBanner) staleBanner.classList.add('hidden');
        return;
    }

    const hasTactics = Boolean(deck.has_tactics);
    const isStale = Boolean(deck.tactics_stale);

    if (badge) {
        if (hasTactics) {
            badge.classList.remove('hidden');
            if (isStale) {
                badge.innerText = 'Stale';
                badge.className = 'hidden sm:inline-block text-[8px] md:text-[9px] px-1 md:px-1.5 py-0.2 rounded-none bg-amber-500/20 text-amber-400 border border-amber-500/30 font-mono font-bold';
            } else {
                badge.innerText = 'Ready';
                badge.className = 'hidden sm:inline-block text-[8px] md:text-[9px] px-1 md:px-1.5 py-0.2 rounded-none bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 font-mono font-bold';
            }
        } else {
            badge.classList.remove('hidden');
            badge.innerText = '5 Intel';
            badge.className = 'hidden sm:inline-block text-[8px] md:text-[9px] px-1 md:px-1.5 py-0.2 rounded-none bg-slate-800 text-slate-400 border border-[#263245] font-mono';
        }
    }
}

async function loadDeckTactics(deckId) {
    if (!deckId) return;
    try {
        const resp = await fetch(`/api/deck/${deckId}/tactics`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        if (data.success) {
            currentDeckTactics = data.tactics || {};
            currentTacticsStatus = data.status || {};
            renderTacticsTab(currentDeckTactics, currentTacticsStatus);
        }
    } catch (e) {
        console.warn("Failed to load tactics:", e);
    }
}

function renderTacticsTab(tactics, status) {
    const emptyState = document.getElementById('tacticsEmptyState');
    const cardsGrid = document.getElementById('tacticsCardsGrid');
    const staleBanner = document.getElementById('tacticsStaleBanner');
    const staleCountBadge = document.getElementById('tacticsStaleCountBadge');
    const sections = (tactics && tactics.sections) || {};

    const hasAnySection = Object.keys(sections).length > 0;
    if (!hasAnySection) {
        if (emptyState) emptyState.classList.remove('hidden');
        if (cardsGrid) cardsGrid.classList.add('hidden');
        if (staleBanner) staleBanner.classList.add('hidden');
        return;
    }

    if (emptyState) emptyState.classList.add('hidden');
    if (cardsGrid) cardsGrid.classList.remove('hidden');

    const staleList = (status && status.stale_sections) || [];
    const isStale = Boolean(status && status.stale);
    if (staleBanner) {
        if (isStale) {
            staleBanner.classList.remove('hidden');
            if (staleCountBadge) {
                staleCountBadge.innerText = `${staleList.length} of ${Object.keys(sections).length} Stale`;
            }
        } else {
            staleBanner.classList.add('hidden');
        }
    }

    const sectionKeys = ['opening_hand', 'game_plan', 'combos', 'threats', 'commander_usage'];
    sectionKeys.forEach(secKey => {
        const secEntry = sections[secKey];
        const isCardStale = staleList.includes(secKey);
        renderTacticsCard(secKey, secEntry, isCardStale);
    });
}

function renderTacticsCard(secKey, entry, isStale) {
    const cardEl = document.getElementById(`tactic-card-${secKey}`);
    if (!cardEl) return;

    if (!entry || !entry.data) {
        cardEl.innerHTML = `
            <div class="flex items-center justify-between border-b border-[#263245] pb-3">
                <div class="flex items-center gap-2">
                    <span class="text-base">${getTacticsIcon(secKey)}</span>
                    <h4 class="text-xs font-mono font-bold text-white uppercase tracking-wider">${escapeHtml(getTacticsTitle(secKey))}</h4>
                </div>
                <button onclick="generateTacticsSection('${secKey}')" class="px-2.5 py-1 bg-cyan-500/10 hover:bg-cyan-500 text-cyan-400 hover:text-slate-950 border border-cyan-500/30 text-[10px] font-mono font-bold uppercase transition rounded-none cursor-pointer">
                    ⚡ Generate
                </button>
            </div>
            <div class="py-6 text-center text-xs font-mono text-slate-500 italic">
                Card not yet generated. Click Generate to run situational AI analysis.
            </div>
        `;
        return;
    }

    const data = entry.data;
    const modelUsed = entry.model_used || 'gemini-3.8-flash';
    const genDate = entry.generated_at ? formatRelativeDate(entry.generated_at) : 'Recent';

    const headerHtml = `
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-[#263245] pb-3">
            <div class="flex items-center gap-2">
                <span class="text-base">${getTacticsIcon(secKey)}</span>
                <h4 class="text-xs font-mono font-bold text-white uppercase tracking-wider">${escapeHtml(getTacticsTitle(secKey))}</h4>
                ${isStale ? `<span class="text-[9px] px-1.5 py-0.2 bg-amber-500/20 text-amber-300 border border-amber-500/40 font-mono font-bold uppercase">Stale</span>` : `<span class="text-[9px] px-1.5 py-0.2 bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 font-mono">Synced</span>`}
            </div>
            <div class="flex items-center gap-2">
                <span class="text-[10px] font-mono text-slate-500">${escapeHtml(genDate)} &bull; ${escapeHtml(modelUsed)}</span>
                <button onclick="generateTacticsSection('${secKey}')" id="btn-regen-${secKey}" title="Regenerate this advice card" class="p-1 hover:bg-slate-800 text-slate-400 hover:text-cyan-400 border border-[#263245] rounded-none transition cursor-pointer">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                </button>
            </div>
        </div>
    `;

    let bodyHtml = '';
    if (secKey === 'opening_hand') {
        bodyHtml = renderOpeningHandBody(data);
    } else if (secKey === 'game_plan') {
        bodyHtml = renderGamePlanBody(data);
    } else if (secKey === 'combos') {
        bodyHtml = renderCombosBody(data);
    } else if (secKey === 'threats') {
        bodyHtml = renderThreatsBody(data);
    } else if (secKey === 'commander_usage') {
        bodyHtml = renderCommanderUsageBody(data);
    }

    cardEl.innerHTML = headerHtml + bodyHtml;
}

function renderCardNamePill(cardName, inDeck = true) {
    const isOut = (inDeck === false);
    const imgUri = resolveCardImageUri(cardName);
    const cls = isOut
        ? "inline-block text-[10px] font-mono px-1.5 py-0.5 rounded-none bg-slate-800/80 text-slate-500 line-through border border-slate-700 cursor-pointer hover:border-slate-500 hover:text-slate-300 transition"
        : "inline-block text-[10px] font-mono px-1.5 py-0.5 rounded-none bg-cyan-950/60 text-cyan-300 border border-cyan-500/30 hover:border-cyan-400 hover:text-white transition cursor-pointer";
    return `<span class="${cls}" data-img="${escapeHtml(imgUri)}" data-card-name="${escapeHtml(cardName)}" onmouseenter="showCardHover(event, this)" onmousemove="moveCardHover(event)" onmouseleave="hideCardHover()" onclick="showCardHover(event, this)" title="${isOut ? 'Not in current deck list' : 'Present in deck'}">${escapeHtml(cardName)}${isOut ? ' (off-list)' : ''}</span>`;
}

function renderOpeningHandBody(data) {
    const summary = data.summary ? `<p class="text-xs text-slate-300 font-sans leading-relaxed">${escapeHtml(data.summary)}</p>` : '';
    
    // Ideal land count badge
    const landCountHtml = data.ideal_land_count ? `
        <div class="flex items-center gap-2 text-xs font-mono bg-cyan-500/10 border border-cyan-500/30 p-2 rounded-none text-cyan-300">
            <span class="font-bold">🎯 Target Land Count:</span>
            <span>${escapeHtml(data.ideal_land_count)}</span>
        </div>
    ` : '';

    // Keep criteria
    const criteria = (data.keep_criteria || []).map(c => `
        <li class="flex items-start gap-1.5 text-xs text-slate-300 font-sans">
            <span class="text-emerald-400 font-bold flex-shrink-0">✓</span>
            <span>${escapeHtml(c)}</span>
        </li>
    `).join('');

    // Priority Cards
    const priority = (data.priority_cards || []).map(pc => `
        <div class="bg-[#0B0F17] p-2 rounded-none border border-[#263245] space-y-1">
            <div>${renderCardNamePill(pc.card_name, pc.in_deck)}</div>
            <p class="text-[11px] text-slate-400 font-sans leading-tight">${escapeHtml(pc.why || '')}</p>
        </div>
    `).join('');

    // Red Flags
    const redFlags = (data.red_flags || []).map(rf => `
        <li class="flex items-start gap-1.5 text-xs text-rose-300 font-sans">
            <span class="text-rose-400 font-bold flex-shrink-0">✗</span>
            <span>${escapeHtml(rf)}</span>
        </li>
    `).join('');

    // Example keeps
    const examples = (data.example_keeps || []).map(ex => {
        const isKeep = (ex.verdict || '').toLowerCase().includes('keep');
        const vCls = isKeep ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40' : 'bg-rose-500/20 text-rose-300 border-rose-500/40';
        const cardsHtml = (ex.hand_detail || (ex.hand || []).map(c => ({ name: c, in_deck: true }))).map(c => renderCardNamePill(c.name, c.in_deck)).join(' ');
        return `
            <div class="bg-[#0B0F17] p-2.5 rounded-none border border-[#263245] space-y-2">
                <div class="flex items-center justify-between">
                    <span class="text-[10px] font-mono text-slate-400 uppercase font-bold">Sample Opening 7</span>
                    <span class="text-[9px] font-mono px-2 py-0.5 rounded-none border font-bold ${vCls}">${escapeHtml(ex.verdict || 'Keep')}</span>
                </div>
                <div class="flex flex-wrap gap-1">${cardsHtml}</div>
                <p class="text-[11px] text-slate-400 font-sans italic border-t border-[#263245] pt-1.5">${escapeHtml(ex.reason || '')}</p>
            </div>
        `;
    }).join('');

    return `
        <div class="space-y-4 pt-1">
            ${summary}
            ${landCountHtml}
            ${criteria ? `
                <div class="space-y-1.5">
                    <h5 class="text-[11px] font-mono font-bold text-emerald-400 uppercase">Keep Criteria:</h5>
                    <ul class="space-y-1">${criteria}</ul>
                </div>
            ` : ''}
            ${priority ? `
                <div class="space-y-1.5">
                    <h5 class="text-[11px] font-mono font-bold text-cyan-400 uppercase">Priority Opening Pieces:</h5>
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">${priority}</div>
                </div>
            ` : ''}
            ${redFlags ? `
                <div class="space-y-1.5">
                    <h5 class="text-[11px] font-mono font-bold text-rose-400 uppercase">Red Flags & Mulligan Triggers:</h5>
                    <ul class="space-y-1">${redFlags}</ul>
                </div>
            ` : ''}
            ${examples ? `
                <div class="space-y-2">
                    <h5 class="text-[11px] font-mono font-bold text-slate-300 uppercase">Sample Mulligan Scenarios:</h5>
                    <div class="space-y-2">${examples}</div>
                </div>
            ` : ''}
        </div>
    `;
}

function renderGamePlanBody(data) {
    const summary = data.summary ? `<p class="text-xs text-slate-300 font-sans leading-relaxed">${escapeHtml(data.summary)}</p>` : '';

    const phases = (data.phases || []).map(p => {
        const goalsHtml = (p.goals || []).map(g => `<li class="text-slate-300 font-sans text-xs flex items-start gap-1"><span class="text-cyan-400 font-bold">&bull;</span><span>${escapeHtml(g)}</span></li>`).join('');
        const playsHtml = (p.key_plays || []).map(kp => `<li class="text-slate-300 font-sans text-xs flex items-start gap-1"><span class="text-emerald-400 font-bold">&rsaquo;</span><span>${escapeHtml(kp)}</span></li>`).join('');
        const avoidHtml = (p.avoid || []).map(a => `<li class="text-rose-300 font-sans text-xs flex items-start gap-1"><span class="text-rose-400 font-bold">&times;</span><span>${escapeHtml(a)}</span></li>`).join('');

        return `
            <div class="bg-[#0B0F17] p-3 rounded-none border border-[#263245] space-y-2.5">
                <div class="flex items-center justify-between border-b border-[#263245] pb-1.5">
                    <span class="text-xs font-mono font-bold text-cyan-400 uppercase">${escapeHtml(p.phase || 'Phase')}</span>
                </div>
                ${goalsHtml ? `<div class="space-y-1"><span class="text-[10px] font-mono uppercase text-slate-400 font-bold">Objectives:</span><ul class="space-y-0.5">${goalsHtml}</ul></div>` : ''}
                ${playsHtml ? `<div class="space-y-1"><span class="text-[10px] font-mono uppercase text-emerald-400 font-bold">Key Sequences:</span><ul class="space-y-0.5">${playsHtml}</ul></div>` : ''}
                ${avoidHtml ? `<div class="space-y-1"><span class="text-[10px] font-mono uppercase text-rose-400 font-bold">Traps to Avoid:</span><ul class="space-y-0.5">${avoidHtml}</ul></div>` : ''}
            </div>
        `;
    }).join('');

    const pivots = (data.pivot_signals || []).map(ps => `
        <li class="flex items-start gap-1.5 text-xs text-amber-200 font-sans">
            <span class="text-amber-400 font-bold">⚡</span>
            <span>${escapeHtml(ps)}</span>
        </li>
    `).join('');

    return `
        <div class="space-y-4 pt-1">
            ${summary}
            <div class="space-y-2.5">${phases}</div>
            ${pivots ? `
                <div class="bg-amber-950/20 border border-amber-500/30 p-2.5 rounded-none space-y-1.5">
                    <span class="text-[10px] font-mono uppercase font-bold text-amber-400">Pacing Pivot Signals:</span>
                    <ul class="space-y-1">${pivots}</ul>
                </div>
            ` : ''}
        </div>
    `;
}

function renderCombosBody(data) {
    const summary = data.summary ? `<p class="text-xs text-slate-300 font-sans leading-relaxed">${escapeHtml(data.summary)}</p>` : '';

    const combos = (data.combos || []).map(c => {
        const cardsHtml = (c.cards_detail || (c.cards || []).map(card => ({ name: card, in_deck: true }))).map(card => renderCardNamePill(card.name, card.in_deck)).join(' ');
        const stepsHtml = (c.steps || []).map((s, idx) => `
            <li class="flex items-start gap-1.5 text-xs text-slate-300 font-sans">
                <span class="text-purple-400 font-bold font-mono text-[10px]">${idx + 1}.</span>
                <span>${escapeHtml(s)}</span>
            </li>
        `).join('');

        return `
            <div class="bg-[#0B0F17] p-3.5 rounded-none border border-purple-500/30 hover:border-purple-500/60 transition space-y-3">
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-1.5 border-b border-[#263245] pb-2">
                    <h5 class="text-xs font-mono font-bold text-white uppercase tracking-wide flex items-center gap-1.5">
                        <span class="text-purple-400">⚡</span>
                        <span>${escapeHtml(c.name || 'Combo Line')}</span>
                    </h5>
                    <div class="flex items-center gap-2">
                        <span class="text-[9px] font-mono px-2 py-0.5 rounded-none bg-purple-500/20 text-purple-300 border border-purple-500/40 font-bold">${escapeHtml(c.type || 'Combo')}</span>
                        ${c.result ? `<span class="text-[9px] font-mono px-2 py-0.5 rounded-none bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-bold">${escapeHtml(c.result)}</span>` : ''}
                    </div>
                </div>

                <div class="space-y-1">
                    <span class="text-[10px] font-mono uppercase text-slate-400 font-bold">Required Pieces:</span>
                    <div class="flex flex-wrap gap-1">${cardsHtml}</div>
                </div>

                ${stepsHtml ? `
                    <div class="space-y-1">
                        <span class="text-[10px] font-mono uppercase text-slate-400 font-bold">Execution Steps:</span>
                        <ul class="space-y-1 bg-[#10141D] p-2.5 rounded-none border border-[#263245]">${stepsHtml}</ul>
                    </div>
                ` : ''}

                <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs font-mono pt-1 border-t border-[#263245]">
                    ${c.tutors_or_redundancy ? `<div><span class="text-slate-500 block text-[9px] uppercase">Tutors & Redundancy:</span><span class="text-slate-300 font-sans text-[11px]">${escapeHtml(Array.isArray(c.tutors_or_redundancy) ? c.tutors_or_redundancy.join(', ') : c.tutors_or_redundancy)}</span></div>` : ''}
                    ${c.vulnerabilities ? `<div><span class="text-rose-400 block text-[9px] uppercase">Vulnerabilities:</span><span class="text-rose-200/80 font-sans text-[11px]">${escapeHtml(c.vulnerabilities)}</span></div>` : ''}
                </div>
            </div>
        `;
    }).join('');

    const near = (data.near_combos || []).map(nc => {
        const inDeckHtml = (nc.cards_in_deck || []).map(c => renderCardNamePill(c, true)).join(' ');
        const missingName = nc.missing || '';
        return `
            <div class="bg-[#0B0F17] p-3 rounded-none border border-amber-500/30 space-y-2">
                <div class="flex items-center justify-between">
                    <span class="text-xs font-mono font-bold text-amber-300 uppercase">${escapeHtml(nc.name || 'Near Combo')}</span>
                    <span class="text-[9px] font-mono px-1.5 py-0.2 bg-amber-500/20 text-amber-300 border border-amber-500/40">Missing 1 Card</span>
                </div>
                <div class="text-[11px] font-mono text-slate-400">
                    <span>Already in Deck:</span>
                    <div class="flex flex-wrap gap-1 mt-0.5">${inDeckHtml}</div>
                </div>
                <div class="text-[11px] font-mono text-amber-300 pt-1 border-t border-[#263245] flex items-center justify-between">
                    <div class="flex items-center gap-1.5 flex-wrap">
                        <span>Missing Piece:</span>
                        ${missingName ? renderCardNamePill(missingName, false) : '<span class="text-slate-500">None</span>'}
                    </div>
                    <span class="text-[10px] text-slate-400">${escapeHtml(nc.result || '')}</span>
                </div>
                ${missingName ? `
                <div class="flex items-center gap-2 pt-1 border-t border-[#263245]/60">
                    <button type="button" onclick="handleSlotComboPiece(this)"
                            data-card-in="${escapeHtml(missingName)}"
                            data-card-out=""
                            class="px-2 py-0.5 rounded-none text-[9px] font-mono font-bold uppercase transition bg-cyan-600 hover:bg-cyan-500 text-slate-950 flex items-center gap-1 cursor-pointer">
                        <span>⚡ + Add to Deck</span>
                    </button>
                    <button type="button" onclick="handleUpgradeWatchClick(this)"
                            data-name="${escapeHtml(missingName)}"
                            class="px-2 py-0.5 rounded-none text-[9px] font-mono font-bold uppercase transition bg-[#1B2230] hover:bg-cyan-500 hover:text-slate-950 text-cyan-400 border border-[#263245] flex items-center gap-1 cursor-pointer">
                        <span>+ Watch</span>
                    </button>
                </div>
                ` : ''}
            </div>
        `;
    }).join('');

    return `
        <div class="space-y-4 pt-1">
            ${summary}
            <div class="space-y-3">${combos || '<div class="text-xs font-mono text-slate-500 italic p-3 bg-[#0B0F17] border border-[#263245]">No complete infinite combos identified in current deck list.</div>'}</div>
            ${near ? `
                <div class="space-y-2 pt-2">
                    <h5 class="text-xs font-mono font-bold text-amber-400 uppercase flex items-center gap-1.5">
                        <span>⚡</span>
                        <span>Near Combos (Missing 1 Card):</span>
                    </h5>
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-2.5">${near}</div>
                </div>
            ` : ''}
        </div>
    `;
}

function renderThreatsBody(data) {
    const summary = data.summary ? `<p class="text-xs text-slate-300 font-sans leading-relaxed">${escapeHtml(data.summary)}</p>` : '';

    const removal = (data.removal_priorities || []).map(rp => {
        const answersHtml = (rp.answers_detail || (rp.answers_in_deck || []).map(c => ({ name: c, in_deck: true }))).map(c => renderCardNamePill(c.name, c.in_deck)).join(' ');
        return `
            <div class="bg-[#0B0F17] p-2.5 rounded-none border border-[#263245] space-y-1.5">
                <div class="flex items-center justify-between">
                    <span class="text-xs font-mono font-bold text-rose-400 uppercase">${escapeHtml(rp.threat || 'Threat')}</span>
                </div>
                <p class="text-[11px] text-slate-300 font-sans">${escapeHtml(rp.why || '')}</p>
                <div class="pt-1 border-t border-[#263245]">
                    <span class="text-[9px] font-mono uppercase text-cyan-400 block mb-0.5">Your Answers in Deck:</span>
                    <div class="flex flex-wrap gap-1">${answersHtml || '<span class="text-[10px] text-slate-500 font-mono italic">None</span>'}</div>
                </div>
            </div>
        `;
    }).join('');

    const matchups = (data.bad_matchups || []).map(bm => `
        <div class="bg-[#0B0F17] p-2.5 rounded-none border border-[#263245] space-y-1">
            <span class="text-xs font-mono font-bold text-amber-300 uppercase">${escapeHtml(bm.archetype || 'Archetype')}</span>
            <p class="text-[11px] text-slate-300 font-sans">${escapeHtml(bm.why || '')}</p>
            <p class="text-[11px] text-emerald-300 font-sans italic border-t border-[#263245] pt-1">Adaptation: ${escapeHtml(bm.how_to_adapt || '')}</p>
        </div>
    `).join('');

    const wipePlan = data.board_wipe_plan ? `
        <div class="bg-[#0B0F17] p-3 rounded-none border border-cyan-500/30 space-y-1">
            <span class="text-[10px] font-mono uppercase font-bold text-cyan-400 flex items-center gap-1">
                <span>🛡️</span>
                <span>Board Wipe Recovery & Navigation Plan:</span>
            </span>
            <p class="text-xs text-slate-300 font-sans leading-relaxed">${escapeHtml(data.board_wipe_plan)}</p>
        </div>
    ` : '';

    return `
        <div class="space-y-4 pt-1">
            ${summary}
            ${removal ? `
                <div class="space-y-1.5">
                    <h5 class="text-[11px] font-mono font-bold text-rose-400 uppercase">Priority Removal Targets:</h5>
                    <div class="space-y-2">${removal}</div>
                </div>
            ` : ''}
            ${wipePlan}
            ${matchups ? `
                <div class="space-y-1.5">
                    <h5 class="text-[11px] font-mono font-bold text-amber-400 uppercase">Challenging Matchups:</h5>
                    <div class="space-y-2">${matchups}</div>
                </div>
            ` : ''}
        </div>
    `;
}

function renderCommanderUsageBody(data) {
    const summary = data.summary ? `<p class="text-xs text-slate-300 font-sans leading-relaxed">${escapeHtml(data.summary)}</p>` : '';

    const badgesHtml = `
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs font-mono">
            <div class="bg-[#0B0F17] p-2.5 rounded-none border border-purple-500/30">
                <span class="text-purple-400 block text-[9px] uppercase font-bold">Ideal Deployment Window:</span>
                <span class="text-white font-bold text-xs">${escapeHtml(data.ideal_cast_turn || 'Turn 3-4')}</span>
            </div>
            <div class="bg-[#0B0F17] p-2.5 rounded-none border border-[#263245]">
                <span class="text-amber-400 block text-[9px] uppercase font-bold">Recast / Tax Threshold:</span>
                <span class="text-slate-300 text-[11px]">${escapeHtml(data.recast_threshold || 'Standard')}</span>
            </div>
        </div>
    `;

    const timingTips = (data.cast_timing_tips || []).map(ct => `
        <li class="flex items-start gap-1.5 text-xs text-slate-300 font-sans">
            <span class="text-purple-400 font-bold">👑</span>
            <span>${escapeHtml(ct)}</span>
        </li>
    `).join('');

    const protection = (data.protection_options || []).map(po => `
        <div class="bg-[#0B0F17] p-2 rounded-none border border-[#263245] space-y-0.5">
            <div>${renderCardNamePill(po.card_name, po.in_deck)}</div>
            <p class="text-[11px] text-slate-400 font-sans">${escapeHtml(po.how || '')}</p>
        </div>
    `).join('');

    const whenNot = (data.when_not_to_cast || []).map(wn => `
        <li class="flex items-start gap-1.5 text-xs text-rose-300 font-sans">
            <span class="text-rose-400 font-bold">&times;</span>
            <span>${escapeHtml(wn)}</span>
        </li>
    `).join('');

    return `
        <div class="space-y-4 pt-1">
            ${summary}
            ${badgesHtml}
            ${timingTips ? `
                <div class="space-y-1.5">
                    <h5 class="text-[11px] font-mono font-bold text-purple-400 uppercase">Deployment Guidelines:</h5>
                    <ul class="space-y-1">${timingTips}</ul>
                </div>
            ` : ''}
            ${protection ? `
                <div class="space-y-1.5">
                    <h5 class="text-[11px] font-mono font-bold text-emerald-400 uppercase">Commander Protection in Deck:</h5>
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">${protection}</div>
                </div>
            ` : ''}
            ${whenNot ? `
                <div class="space-y-1.5">
                    <h5 class="text-[11px] font-mono font-bold text-rose-400 uppercase">When NOT to Cast:</h5>
                    <ul class="space-y-1">${whenNot}</ul>
                </div>
            ` : ''}
        </div>
    `;
}

async function generateTacticsSection(secKey) {
    if (!activeDeck || !activeDeck.id) return;
    const modelSelect = document.getElementById('tacticsAiModel');
    const model = (modelSelect && modelSelect.value) || 'gemini-3.8-flash';

    const cardEl = document.getElementById(`tactic-card-${secKey}`);
    if (cardEl) {
        cardEl.innerHTML = `
            <div class="flex items-center justify-between border-b border-[#263245] pb-3">
                <div class="flex items-center gap-2">
                    <span class="text-base">${getTacticsIcon(secKey)}</span>
                    <h4 class="text-xs font-mono font-bold text-white uppercase tracking-wider">${escapeHtml(getTacticsTitle(secKey))}</h4>
                </div>
                <span class="text-[10px] font-mono text-cyan-400 animate-pulse">Analyzing...</span>
            </div>
            <div class="py-10 text-center space-y-3 font-mono text-xs text-slate-400">
                <div class="inline-block animate-spin text-cyan-400 text-xl">&#x27F3;</div>
                <p>Consulting Gemini ${escapeHtml(model)} for ${escapeHtml(getTacticsTitle(secKey))}...</p>
            </div>
        `;
    }

    try {
        const resp = await fetch(`/api/deck/${activeDeck.id}/tactics/${secKey}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model })
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.error || `Server returned HTTP ${resp.status}`);
        }

        if (!currentDeckTactics) currentDeckTactics = {};
        if (!currentDeckTactics.sections) currentDeckTactics.sections = {};
        currentDeckTactics.sections[secKey] = data.entry;
        currentTacticsStatus = data.status || {};

        activeDeck.has_tactics = true;
        activeDeck.tactics_stale = Boolean(currentTacticsStatus.stale);

        renderTacticsTab(currentDeckTactics, currentTacticsStatus);
        updateTacticsBadgeAndBanner(activeDeck);
        showToast(`${getTacticsTitle(secKey)} generated successfully!`, 'success');
        return true;
    } catch (e) {
        if (cardEl) {
            cardEl.innerHTML = `
                <div class="flex items-center justify-between border-b border-[#263245] pb-3">
                    <div class="flex items-center gap-2">
                        <span class="text-base">${getTacticsIcon(secKey)}</span>
                        <h4 class="text-xs font-mono font-bold text-rose-400 uppercase tracking-wider">${escapeHtml(getTacticsTitle(secKey))}</h4>
                    </div>
                </div>
                <div class="py-6 text-center space-y-2 font-mono text-xs">
                    <p class="text-rose-400">Failed: ${escapeHtml(e.message)}</p>
                    <button onclick="generateTacticsSection('${secKey}')" class="px-3 py-1 bg-rose-500/20 text-rose-300 border border-rose-500/40 uppercase rounded-none hover:bg-rose-500 hover:text-white transition">
                        Retry
                    </button>
                </div>
            `;
        }
        showToast(`Tactics error (${secKey}): ${e.message}`, 'error');
        return false;
    }
}

async function generateAllTactics(options = {}) {
    if (!activeDeck || !activeDeck.id) return;
    const { onlyStale = false } = options;

    const allKeys = ['opening_hand', 'game_plan', 'combos', 'threats', 'commander_usage'];
    const targetKeys = onlyStale
        ? (currentTacticsStatus && currentTacticsStatus.stale_sections && currentTacticsStatus.stale_sections.length > 0 ? currentTacticsStatus.stale_sections : allKeys)
        : allKeys;

    const btnAll = document.getElementById('btnGenerateAllTactics');
    const btnStale = document.getElementById('btnRegenerateStaleTactics');
    const origAllText = btnAll ? btnAll.innerHTML : '';
    const origStaleText = btnStale ? btnStale.innerHTML : '';

    if (btnAll) {
        btnAll.disabled = true;
        btnAll.innerHTML = `<span class="inline-block animate-spin mr-1">&#x27F3;</span> Generating ${targetKeys.length} Cards...`;
    }
    if (btnStale) {
        btnStale.disabled = true;
        btnStale.innerHTML = `<span class="inline-block animate-spin mr-1">&#x27F3;</span> Updating...`;
    }

    try {
        const promises = targetKeys.map(k => generateTacticsSection(k));
        const results = await Promise.allSettled(promises);
        const successes = results.filter(r => r.status === 'fulfilled' && r.value === true).length;
        showToast(`Commander Tactics: ${successes} of ${targetKeys.length} cards updated.`, successes === targetKeys.length ? 'success' : 'info');
        await loadDeckTactics(activeDeck.id);
        if (activeDeck) {
            updateTacticsBadgeAndBanner(activeDeck);
        }
    } finally {
        if (btnAll) {
            btnAll.disabled = false;
            btnAll.innerHTML = origAllText;
        }
        if (btnStale) {
            btnStale.disabled = false;
            btnStale.innerHTML = origStaleText;
        }
    }
}

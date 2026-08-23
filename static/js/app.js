// =========================================================================
// CHIMAERA // IMPERIAL NAVAL INTELLIGENCE CLIENT TELEMETRY & CONTROLS
// =========================================================================

let currentPrintsData = [];
let autocompleteTimeout = null;

// =========================================================================
// Tactical Toast Notification System
// =========================================================================
function showToast(message, type = "success") {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    let borderColor = "border-[#008080]";
    let prefixTag = "[SYS:CONFIRMED]";
    let textColor = "text-[#00CED1]";

    if (type === "error") {
        borderColor = "border-[#DC143C]";
        prefixTag = "[SYS:ALERT]";
        textColor = "text-[#FF3358]";
    } else if (type === "info") {
        borderColor = "border-[#384860]";
        prefixTag = "[SYS:TELEMETRY]";
        textColor = "text-[#8E9BAE]";
    }
    
    toast.className = `bg-[#1B2230] ${borderColor} border text-[#E0E0E0] text-xs px-4 py-3 font-mono flex items-center space-x-2 transition-all duration-200 transform translate-y-2 opacity-0 pointer-events-auto`;
    toast.innerHTML = `
        <span class="${textColor} font-bold text-[10px] uppercase tracking-wider">${prefixTag}</span>
        <span class="tracking-tight text-[11px]">${message}</span>
    `;

    container.appendChild(toast);

    // Animate in
    requestAnimationFrame(() => {
        toast.classList.remove("translate-y-2", "opacity-0");
    });

    // Auto remove after 3.8 seconds
    setTimeout(() => {
        toast.classList.add("opacity-0", "translate-y-2");
        setTimeout(() => toast.remove(), 250);
    }, 3800);
}

// =========================================================================
// Modal Controls
// =========================================================================
function openAddCardModal() {
    const modal = document.getElementById("modal-add-card");
    if (modal) {
        modal.classList.remove("hidden");
        document.getElementById("card-search-input").focus();
    }
}

function closeAddCardModal() {
    const modal = document.getElementById("modal-add-card");
    if (modal) {
        modal.classList.add("hidden");
        document.getElementById("card-search-input").value = "";
        document.getElementById("autocomplete-dropdown").classList.add("hidden");
        document.getElementById("print-selector-container").classList.add("hidden");
        document.getElementById("btn-submit-add-card").disabled = true;
        currentPrintsData = [];
    }
}

function openEditTargetModal(id, name, currentTarget) {
    const modal = document.getElementById("modal-edit-target");
    if (!modal) return;

    document.getElementById("edit-target-item-id").value = id;
    document.getElementById("edit-target-card-name").textContent = `TARGET: ${name}`;
    document.getElementById("edit-target-price-input").value = currentTarget !== null ? currentTarget : "";
    modal.classList.remove("hidden");
    document.getElementById("edit-target-price-input").focus();
}

function closeEditTargetModal() {
    const modal = document.getElementById("modal-edit-target");
    if (modal) modal.classList.add("hidden");
}

// =========================================================================
// Autocomplete & Print Selection
// =========================================================================
document.addEventListener("DOMContentLoaded", () => {
    const searchInput = document.getElementById("card-search-input");
    const dropdown = document.getElementById("autocomplete-dropdown");
    const printSelect = document.getElementById("card-print-select");
    const finishSelect = document.getElementById("card-finish-select");

    if (searchInput) {
        searchInput.addEventListener("input", (e) => {
            const query = e.target.value.trim();
            clearTimeout(autocompleteTimeout);

            if (query.length < 2) {
                dropdown.classList.add("hidden");
                dropdown.innerHTML = "";
                return;
            }

            autocompleteTimeout = setTimeout(async () => {
                try {
                    const res = await fetch(`/api/scryfall/autocomplete?q=${encodeURIComponent(query)}`);
                    const data = await res.json();
                    const suggestions = data.suggestions || [];

                    if (suggestions.length === 0) {
                        dropdown.innerHTML = `<div class="p-2.5 text-[11px] font-mono text-[#8E9BAE] text-center uppercase">[ NO MATCHING TARGETS IDENTIFIED ]</div>`;
                    } else {
                        dropdown.innerHTML = suggestions.map(name => `
                            <div class="px-3 py-2 text-xs font-mono text-[#E0E0E0] hover:bg-[#222B3D] hover:text-[#00CED1] cursor-pointer transition flex items-center justify-between border-b border-[#263245]/50"
                                 onclick="selectCardName('${name.replace(/'/g, "\\'")}')">
                                <span>${name}</span>
                                <span class="text-[10px] text-[#008080] font-bold">&rarr;</span>
                            </div>
                        `).join("");
                    }
                    dropdown.classList.remove("hidden");
                } catch (err) {
                    console.error("Autocomplete error:", err);
                }
            }, 250);
        });
    }

    if (printSelect) {
        printSelect.addEventListener("change", (e) => {
            const selectedId = e.target.value;
            const printObj = currentPrintsData.find(p => p.id === selectedId);
            if (printObj) {
                updateSelectedPrintView(printObj);
            }
        });
    }

    if (finishSelect) {
        finishSelect.addEventListener("change", () => {
            const selectedId = printSelect.value;
            const printObj = currentPrintsData.find(p => p.id === selectedId);
            if (printObj) {
                updateSelectedPrintView(printObj);
            }
        });
    }

    // Close autocomplete on click outside
    document.addEventListener("click", (e) => {
        if (searchInput && dropdown && !searchInput.contains(e.target) && !dropdown.contains(e.target)) {
            dropdown.classList.add("hidden");
        }
    });
});

async function selectCardName(cardName) {
    const searchInput = document.getElementById("card-search-input");
    const dropdown = document.getElementById("autocomplete-dropdown");
    const printContainer = document.getElementById("print-selector-container");
    const printSelect = document.getElementById("card-print-select");
    const submitBtn = document.getElementById("btn-submit-add-card");

    if (searchInput) searchInput.value = cardName;
    if (dropdown) dropdown.classList.add("hidden");

    // Fetch Prints
    try {
        const res = await fetch(`/api/scryfall/prints?name=${encodeURIComponent(cardName)}`);
        const data = await res.json();
        currentPrintsData = data.prints || [];

        if (currentPrintsData.length === 0) {
            showToast("No prints found for target query", "error");
            return;
        }

        // Populate print selector
        printSelect.innerHTML = currentPrintsData.map(p => `
            <option value="${p.id}">
                ${p.set_name} (${p.set_code.toUpperCase()}) #${p.collector_number} [${p.rarity.toUpperCase()}] - ${p.released_at ? p.released_at.substring(0, 4) : ''}
            </option>
        `).join("");

        printContainer.classList.remove("hidden");
        submitBtn.disabled = false;

        // Render first print by default
        updateSelectedPrintView(currentPrintsData[0]);

    } catch (err) {
        console.error("Error loading prints:", err);
        showToast("Failed to load printing classifications", "error");
    }
}

function updateSelectedPrintView(printObj) {
    const imgElem = document.getElementById("card-preview-img");
    const titleElem = document.getElementById("card-preview-title");
    const metaElem = document.getElementById("card-preview-meta");
    const priceElem = document.getElementById("card-preview-price");
    const finishSelect = document.getElementById("card-finish-select");

    if (imgElem) {
        imgElem.src = printObj.image_uri || "";
        imgElem.alt = printObj.name;
    }
    if (titleElem) titleElem.textContent = printObj.name;
    if (metaElem) metaElem.textContent = `${printObj.set_name} (${printObj.set_code.toUpperCase()}) #${printObj.collector_number} // ${printObj.rarity.toUpperCase()}`;

    // Update finish dropdown availability
    const availableFinishes = printObj.finishes || ["nonfoil"];
    Array.from(finishSelect.options).forEach(opt => {
        if (availableFinishes.includes(opt.value)) {
            opt.disabled = false;
            opt.textContent = opt.value.toUpperCase();
        } else {
            opt.disabled = true;
            opt.textContent = `${opt.value.toUpperCase()} (UNAVAILABLE)`;
        }
    });

    if (!availableFinishes.includes(finishSelect.value)) {
        finishSelect.value = availableFinishes[0];
    }

    const selectedFinish = finishSelect.value;
    let estPrice = "N/A";
    if (selectedFinish === "foil" && printObj.prices.usd_foil) {
        estPrice = `$${printObj.prices.usd_foil}`;
    } else if (selectedFinish === "etched" && printObj.prices.usd_etched) {
        estPrice = `$${printObj.prices.usd_etched}`;
    } else if (printObj.prices.usd) {
        estPrice = `$${printObj.prices.usd}`;
    }

    if (priceElem) priceElem.textContent = `TCGPLAYER MARKET TELEMETRY: ${estPrice}`;
}

// =========================================================================
// Add Card Submit Action
// =========================================================================
async function submitAddCard() {
    const printSelect = document.getElementById("card-print-select");
    const finishSelect = document.getElementById("card-finish-select");
    const targetPriceInput = document.getElementById("card-target-price-input");
    const submitBtn = document.getElementById("btn-submit-add-card");

    const selectedId = printSelect.value;
    const printObj = currentPrintsData.find(p => p.id === selectedId);

    if (!printObj) {
        showToast("Select a valid target printing.", "error");
        return;
    }

    const payload = {
        scryfall_id: printObj.id,
        name: printObj.name,
        set_code: printObj.set_code,
        collector_number: printObj.collector_number,
        image_uri: printObj.image_uri,
        finish: finishSelect.value,
        target_price: targetPriceInput.value ? parseFloat(targetPriceInput.value) : null,
    };

    submitBtn.disabled = true;
    submitBtn.textContent = "Scanning Telemetry...";

    try {
        const res = await fetch("/api/watchlist/add", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        const data = await res.json();
        if (res.ok) {
            showToast(data.message || `Target acquired: ${printObj.name}`, "success");
            closeAddCardModal();
            setTimeout(() => window.location.reload(), 500);
        } else {
            showToast(data.error || "Failed to register target", "error");
            submitBtn.disabled = false;
            submitBtn.textContent = "Add to Watchlist";
        }
    } catch (err) {
        console.error("Add card error:", err);
        showToast("Communication failure adding target", "error");
        submitBtn.disabled = false;
        submitBtn.textContent = "Add to Watchlist";
    }
}

// =========================================================================
// Edit Target Price Submit Action
// =========================================================================
async function submitEditTarget() {
    const itemId = document.getElementById("edit-target-item-id").value;
    const targetPriceVal = document.getElementById("edit-target-price-input").value;

    try {
        const res = await fetch(`/api/watchlist/update-target/${itemId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                target_price: targetPriceVal ? parseFloat(targetPriceVal) : null,
            }),
        });

        const data = await res.json();
        if (res.ok) {
            showToast("Target threshold reconfigured.", "success");
            closeEditTargetModal();
            setTimeout(() => window.location.reload(), 450);
        } else {
            showToast(data.error || "Failed to reconfigure threshold", "error");
        }
    } catch (err) {
        console.error("Update target error:", err);
        showToast("Communication error updating target", "error");
    }
}

// =========================================================================
// Refresh Single Card & Refresh All
// =========================================================================
async function triggerRefreshCard(itemId) {
    const icon = document.querySelector(`.refresh-icon-${itemId}`);
    if (icon) icon.classList.add("animate-spin-custom");

    try {
        const res = await fetch(`/api/watchlist/refresh/${itemId}`, { method: "POST" });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || "Target price intel refreshed", "success");
            setTimeout(() => window.location.reload(), 450);
        } else {
            showToast(data.error || "Failed to refresh target intel", "error");
        }
    } catch (err) {
        console.error("Refresh error:", err);
        showToast("Failed to refresh card telemetry", "error");
    } finally {
        if (icon) icon.classList.remove("animate-spin-custom");
    }
}

async function triggerRefreshAll() {
    const icon = document.getElementById("refresh-all-icon");
    const btn = document.getElementById("btn-refresh-all");
    if (icon) icon.classList.add("animate-spin-custom");
    if (btn) btn.disabled = true;

    showToast("Executing fleet price surveillance scan...", "info");

    try {
        const res = await fetch("/api/watchlist/refresh-all", { method: "POST" });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || "Fleet telemetry synchronized", "success");
            setTimeout(() => window.location.reload(), 600);
        } else {
            showToast(data.error || "Fleet poll failed", "error");
        }
    } catch (err) {
        console.error("Refresh all error:", err);
        showToast("Surveillance poll failure", "error");
    } finally {
        if (icon) icon.classList.remove("animate-spin-custom");
        if (btn) btn.disabled = false;
    }
}

// =========================================================================
// Delete Card Action
// =========================================================================
async function deleteCard(itemId, cardName) {
    if (!confirm(`CONFIRM TARGET DE-REGISTRATION:\nRemove "${cardName}" from surveillance registry?`)) {
        return;
    }

    try {
        const res = await fetch(`/api/watchlist/delete/${itemId}`, { method: "DELETE" });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || `Target ${cardName} de-registered`, "success");
            const elem = document.getElementById(`card-row-${itemId}`);
            if (elem) {
                elem.style.transition = "opacity 0.25s, transform 0.25s";
                elem.style.opacity = "0";
                elem.style.transform = "scale(0.96)";
                setTimeout(() => window.location.reload(), 300);
            } else {
                window.location.reload();
            }
        } else {
            showToast(data.error || "Failed to remove target", "error");
        }
    } catch (err) {
        console.error("Delete error:", err);
        showToast("Error removing target", "error");
    }
}

// =========================================================================
// Filter & Search Watchlist Client-side
// =========================================================================
function filterWatchlist() {
    const searchVal = (document.getElementById("watchlist-search")?.value || "").toLowerCase().trim();
    const dealFilter = document.getElementById("watchlist-filter-deal")?.value || "all";
    const cards = document.querySelectorAll(".watchlist-card");

    cards.forEach(card => {
        const name = card.dataset.name || "";
        const set = card.dataset.set || "";
        const isDeal = card.dataset.isDeal === "true";
        const inStock = card.dataset.inStock === "true";

        const matchesSearch = !searchVal || name.includes(searchVal) || set.includes(searchVal);
        let matchesFilter = true;

        if (dealFilter === "deals") {
            matchesFilter = isDeal;
        } else if (dealFilter === "in_stock") {
            matchesFilter = inStock;
        }

        if (matchesSearch && matchesFilter) {
            card.classList.remove("hidden");
        } else {
            card.classList.add("hidden");
        }
    });
}

// =========================================================================
// Discord Webhook Test Action
// =========================================================================
async function testDiscordWebhook() {
    showToast("Transmitting Discord telemetry ping...", "info");
    try {
        const res = await fetch("/api/discord/test", { method: "POST" });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, "success");
        } else {
            showToast(data.error || "Transmission failed", "error");
        }
    } catch (err) {
        showToast("Transmission error to Discord endpoint", "error");
    }
}


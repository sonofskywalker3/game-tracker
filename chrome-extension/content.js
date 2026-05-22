// Detect game titles from various store pages
function detectGameTitle() {
  const url = window.location.href;
  let title = '';

  // PlayStation Store
  if (url.includes('store.playstation.com')) {
    // Product page
    const psTitle = document.querySelector('[data-qa="mfe-game-title#name"]');
    if (psTitle) title = psTitle.textContent.trim();

    // Fallback selectors
    if (!title) {
      const h1 = document.querySelector('h1');
      if (h1) title = h1.textContent.trim();
    }
  }

  // Xbox / Microsoft Store
  else if (url.includes('xbox.com') || url.includes('microsoft.com/store')) {
    const xboxTitle = document.querySelector('h1[class*="ProductTitle"]') ||
                      document.querySelector('h1[id*="title"]') ||
                      document.querySelector('h1');
    if (xboxTitle) title = xboxTitle.textContent.trim();
  }

  // Steam
  else if (url.includes('store.steampowered.com')) {
    const steamTitle = document.querySelector('#appHubAppName') ||
                       document.querySelector('.apphub_AppName');
    if (steamTitle) title = steamTitle.textContent.trim();
  }

  // Nintendo
  else if (url.includes('nintendo.com')) {
    const nintendoTitle = document.querySelector('h1.product-title') ||
                          document.querySelector('h1[class*="Heading"]') ||
                          document.querySelector('h1');
    if (nintendoTitle) title = nintendoTitle.textContent.trim();
  }

  // GOG
  else if (url.includes('gog.com')) {
    const gogTitle = document.querySelector('.productcard-basics__title') ||
                     document.querySelector('h1');
    if (gogTitle) title = gogTitle.textContent.trim();
  }

  // Humble Bundle
  else if (url.includes('humblebundle.com')) {
    const humbleTitle = document.querySelector('.human-name') ||
                        document.querySelector('h1');
    if (humbleTitle) title = humbleTitle.textContent.trim();
  }

  return title;
}

// Detect platform from URL
function detectPlatform() {
  const url = window.location.href;

  if (url.includes('store.playstation.com')) {
    // Try to detect PS4 vs PS5 from page content
    const pageText = document.body.innerText;
    const platforms = [];
    if (pageText.includes('PS5') || pageText.includes('PlayStation 5')) platforms.push('PS5');
    if (pageText.includes('PS4') || pageText.includes('PlayStation 4')) platforms.push('PS4');
    return platforms.length ? platforms : ['PS5']; // Default to PS5
  }

  if (url.includes('xbox.com') || url.includes('microsoft.com/store')) {
    return ['Xbox'];
  }

  if (url.includes('nintendo.com')) {
    return ['Switch'];
  }

  // Steam and others - no default platform
  return [];
}

// Create and show the add game dialog
function showAddDialog(suggestedTitle = '') {
  // Remove existing dialog if any
  const existing = document.getElementById('game-tracker-dialog');
  if (existing) existing.remove();

  const detectedTitle = suggestedTitle || detectGameTitle();
  const detectedPlatforms = detectPlatform();

  const dialog = document.createElement('div');
  dialog.id = 'game-tracker-dialog';
  dialog.innerHTML = `
    <div style="position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 999999; display: flex; align-items: center; justify-content: center; font-family: system-ui, -apple-system, sans-serif;">
      <div style="background: #1a1a2e; border-radius: 12px; padding: 24px; max-width: 400px; width: 90%; box-shadow: 0 25px 50px rgba(0,0,0,0.5);">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
          <h2 style="color: white; font-size: 18px; font-weight: 600; margin: 0;">Add to Game Tracker</h2>
          <button id="gt-close" style="background: none; border: none; color: #999; font-size: 24px; cursor: pointer; padding: 0; line-height: 1;">&times;</button>
        </div>

        <div style="margin-bottom: 16px;">
          <label style="display: block; color: #999; font-size: 14px; margin-bottom: 6px;">Game Title</label>
          <input type="text" id="gt-title" value="${detectedTitle.replace(/"/g, '&quot;')}"
                 style="width: 100%; background: #25253d; border: 1px solid #444; border-radius: 8px; padding: 10px 12px; color: white; font-size: 14px; box-sizing: border-box;"
                 placeholder="Enter game title...">
          <div id="gt-suggestions" style="margin-top: 4px; background: #25253d; border: 1px solid #444; border-radius: 8px; max-height: 150px; overflow-y: auto; display: none;"></div>
        </div>

        <div style="margin-bottom: 20px;">
          <label style="display: block; color: #999; font-size: 14px; margin-bottom: 6px;">Platforms</label>
          <div style="display: flex; gap: 12px; flex-wrap: wrap;">
            ${['PS4', 'PS5', 'Switch', 'Xbox'].map(p => `
              <label style="display: flex; align-items: center; gap: 6px; cursor: pointer;">
                <input type="checkbox" class="gt-platform" value="${p}" ${detectedPlatforms.includes(p) ? 'checked' : ''}
                       style="accent-color: #7c3aed; width: 16px; height: 16px;">
                <span style="color: white; font-size: 14px;">${p}</span>
              </label>
            `).join('')}
          </div>
        </div>

        <div id="gt-status" style="margin-bottom: 16px; padding: 10px; border-radius: 6px; font-size: 14px; display: none;"></div>

        <div style="display: flex; gap: 12px;">
          <button id="gt-cancel" style="flex: 1; padding: 10px; background: #333; border: none; border-radius: 8px; color: white; font-size: 14px; cursor: pointer;">Cancel</button>
          <button id="gt-add" style="flex: 1; padding: 10px; background: #7c3aed; border: none; border-radius: 8px; color: white; font-size: 14px; cursor: pointer; font-weight: 500;">Add Game</button>
        </div>
      </div>
    </div>
  `;

  document.body.appendChild(dialog);

  // Focus title input
  const titleInput = document.getElementById('gt-title');
  titleInput.focus();
  titleInput.select();

  // Event handlers
  document.getElementById('gt-close').onclick = () => dialog.remove();
  document.getElementById('gt-cancel').onclick = () => dialog.remove();

  // Click outside to close
  dialog.firstElementChild.onclick = (e) => {
    if (e.target === dialog.firstElementChild) dialog.remove();
  };

  // IGDB search as you type
  let searchTimeout;
  titleInput.oninput = () => {
    clearTimeout(searchTimeout);
    const query = titleInput.value.trim();
    if (query.length < 2) {
      document.getElementById('gt-suggestions').style.display = 'none';
      return;
    }

    searchTimeout = setTimeout(() => {
      chrome.runtime.sendMessage({ action: 'searchIGDB', query }, (results) => {
        const suggestionsDiv = document.getElementById('gt-suggestions');
        if (!results || results.error || !results.length) {
          suggestionsDiv.style.display = 'none';
          return;
        }

        suggestionsDiv.innerHTML = results.slice(0, 5).map(game => `
          <div class="gt-suggestion" data-name="${game.name.replace(/"/g, '&quot;')}" data-cover="${game.cover_url || ''}"
               style="padding: 8px 12px; cursor: pointer; display: flex; align-items: center; gap: 8px; border-bottom: 1px solid #333;">
            ${game.cover_url ? `<img src="${game.cover_url}" style="width: 30px; height: 40px; object-fit: cover; border-radius: 4px;">` : ''}
            <span style="color: white; font-size: 13px;">${game.name}</span>
          </div>
        `).join('');
        suggestionsDiv.style.display = 'block';

        // Click suggestion to select
        suggestionsDiv.querySelectorAll('.gt-suggestion').forEach(el => {
          el.onmouseenter = () => el.style.background = '#333';
          el.onmouseleave = () => el.style.background = 'transparent';
          el.onclick = () => {
            titleInput.value = el.dataset.name;
            titleInput.dataset.coverUrl = el.dataset.cover;
            suggestionsDiv.style.display = 'none';
          };
        });
      });
    }, 300);
  };

  // Add game
  document.getElementById('gt-add').onclick = async () => {
    const title = titleInput.value.trim();
    if (!title) {
      showStatus('Please enter a game title', 'error');
      return;
    }

    const platforms = Array.from(document.querySelectorAll('.gt-platform:checked')).map(cb => cb.value);
    const addBtn = document.getElementById('gt-add');
    addBtn.textContent = 'Adding...';
    addBtn.disabled = true;

    chrome.runtime.sendMessage({ action: 'addGame', title, platforms }, (result) => {
      if (result.success) {
        showStatus('Game added successfully!', 'success');
        setTimeout(() => dialog.remove(), 1500);
      } else if (result.exists) {
        showStatus('Game already in your library', 'warning');
        addBtn.textContent = 'Add Game';
        addBtn.disabled = false;
      } else {
        showStatus(result.error || 'Failed to add game', 'error');
        addBtn.textContent = 'Add Game';
        addBtn.disabled = false;
      }
    });
  };

  // Enter to submit
  titleInput.onkeydown = (e) => {
    if (e.key === 'Enter') {
      document.getElementById('gt-suggestions').style.display = 'none';
      document.getElementById('gt-add').click();
    }
    if (e.key === 'Escape') {
      dialog.remove();
    }
  };

  function showStatus(message, type) {
    const status = document.getElementById('gt-status');
    status.textContent = message;
    status.style.display = 'block';
    status.style.background = type === 'success' ? '#166534' : type === 'warning' ? '#854d0e' : '#7f1d1d';
    status.style.color = type === 'success' ? '#86efac' : type === 'warning' ? '#fcd34d' : '#fca5a5';
  }
}

// Listen for messages from background script
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'showAddDialog') {
    showAddDialog(request.suggestedTitle);
    sendResponse({ success: true });
  }

  if (request.action === 'getDetectedTitle') {
    const title = detectGameTitle();
    const platforms = detectPlatform();
    sendResponse({ title, platforms });
  }

  return true;
});

// Watch for purchase confirmation pages
function checkForPurchaseConfirmation() {
  const url = window.location.href;

  // PlayStation purchase confirmation
  if (url.includes('store.playstation.com') &&
      (url.includes('/transaction/') || url.includes('receipt') || document.body.innerText.includes('Thank you for your purchase'))) {
    notifyPurchaseDetected();
  }

  // Xbox purchase confirmation
  if ((url.includes('xbox.com') || url.includes('microsoft.com')) &&
      (url.includes('/order/') || url.includes('orderconfirmation') || document.body.innerText.includes('Order confirmed'))) {
    notifyPurchaseDetected();
  }
}

function notifyPurchaseDetected() {
  // Show a subtle prompt
  const prompt = document.createElement('div');
  prompt.id = 'gt-purchase-prompt';
  prompt.innerHTML = `
    <div style="position: fixed; bottom: 20px; right: 20px; background: #7c3aed; padding: 16px 20px; border-radius: 12px; box-shadow: 0 10px 40px rgba(0,0,0,0.3); z-index: 999999; display: flex; align-items: center; gap: 12px; font-family: system-ui, -apple-system, sans-serif;">
      <span style="color: white; font-size: 14px;">New purchase detected!</span>
      <button id="gt-prompt-add" style="background: white; color: #7c3aed; border: none; padding: 8px 16px; border-radius: 6px; font-weight: 500; cursor: pointer; font-size: 13px;">Add to Library</button>
      <button id="gt-prompt-dismiss" style="background: none; border: none; color: white; opacity: 0.7; cursor: pointer; font-size: 18px; padding: 0;">&times;</button>
    </div>
  `;

  // Remove existing prompt
  const existing = document.getElementById('gt-purchase-prompt');
  if (existing) existing.remove();

  document.body.appendChild(prompt);

  document.getElementById('gt-prompt-add').onclick = () => {
    prompt.remove();
    showAddDialog();
  };

  document.getElementById('gt-prompt-dismiss').onclick = () => {
    prompt.remove();
  };

  // Auto-dismiss after 10 seconds
  setTimeout(() => prompt.remove(), 10000);
}

// Run on page load
setTimeout(checkForPurchaseConfirmation, 2000);

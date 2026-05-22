const titleInput = document.getElementById('title');
const suggestionsDiv = document.getElementById('suggestions');
const statusDiv = document.getElementById('status');
const addBtn = document.getElementById('addBtn');
const detectBtn = document.getElementById('detectBtn');
const serverUrlInput = document.getElementById('serverUrl');

let selectedCoverUrl = '';
let searchTimeout;

// Load saved server URL
chrome.storage.sync.get(['serverUrl'], (result) => {
  serverUrlInput.value = result.serverUrl || 'http://localhost:5000';
});

// Save server URL on change
serverUrlInput.addEventListener('change', () => {
  chrome.storage.sync.set({ serverUrl: serverUrlInput.value.trim() });
});

// Search IGDB as you type
titleInput.addEventListener('input', () => {
  clearTimeout(searchTimeout);
  const query = titleInput.value.trim();

  if (query.length < 2) {
    suggestionsDiv.style.display = 'none';
    return;
  }

  searchTimeout = setTimeout(() => {
    chrome.runtime.sendMessage({ action: 'searchIGDB', query }, (results) => {
      if (!results || results.error || !results.length) {
        suggestionsDiv.style.display = 'none';
        return;
      }

      suggestionsDiv.innerHTML = results.slice(0, 5).map(game => `
        <div class="suggestion" data-name="${game.name.replace(/"/g, '&quot;')}" data-cover="${game.cover_url || ''}">
          ${game.cover_url ? `<img src="${game.cover_url}">` : ''}
          <span>${game.name}</span>
        </div>
      `).join('');
      suggestionsDiv.style.display = 'block';

      suggestionsDiv.querySelectorAll('.suggestion').forEach(el => {
        el.addEventListener('click', () => {
          titleInput.value = el.dataset.name;
          selectedCoverUrl = el.dataset.cover;
          suggestionsDiv.style.display = 'none';
        });
      });
    });
  }, 300);
});

// Detect from current page
detectBtn.addEventListener('click', async () => {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    chrome.tabs.sendMessage(tab.id, { action: 'getDetectedTitle' }, (response) => {
      if (chrome.runtime.lastError) {
        showStatus('Cannot detect on this page', 'error');
        return;
      }
      if (response && response.title) {
        titleInput.value = response.title;
        if (response.platforms) {
          document.querySelectorAll('.platform').forEach(cb => {
            cb.checked = response.platforms.includes(cb.value);
          });
        }
        showStatus('Detected from page', 'success');
        setTimeout(() => statusDiv.style.display = 'none', 2000);
      } else {
        showStatus('No game detected on this page', 'warning');
      }
    });
  } catch (e) {
    showStatus('Cannot detect on this page', 'error');
  }
});

// Add game
addBtn.addEventListener('click', () => {
  const title = titleInput.value.trim();
  if (!title) {
    showStatus('Please enter a game title', 'error');
    return;
  }

  const platforms = Array.from(document.querySelectorAll('.platform:checked')).map(cb => cb.value);

  addBtn.textContent = 'Adding...';
  addBtn.disabled = true;

  chrome.runtime.sendMessage({ action: 'addGame', title, platforms }, (result) => {
    addBtn.textContent = 'Add Game';
    addBtn.disabled = false;

    if (result.success) {
      showStatus('Game added!', 'success');
      titleInput.value = '';
      document.querySelectorAll('.platform').forEach(cb => cb.checked = false);
      selectedCoverUrl = '';
    } else if (result.exists) {
      showStatus('Game already in library', 'warning');
    } else {
      showStatus(result.error || 'Failed to add', 'error');
    }
  });
});

// Enter to submit
titleInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    suggestionsDiv.style.display = 'none';
    addBtn.click();
  }
});

function showStatus(message, type) {
  statusDiv.textContent = message;
  statusDiv.className = 'status ' + type;
  statusDiv.style.display = 'block';
}

// Also listen for getDetectedTitle in content script
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'getDetectedTitle') {
    // This is handled by content.js, but we need to add the handler there
  }
});

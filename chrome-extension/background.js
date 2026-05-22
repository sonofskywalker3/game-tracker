// Create context menu on install
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'add-to-game-tracker',
    title: 'Add to Game Tracker',
    contexts: ['page', 'selection', 'link']
  });
});

// Handle context menu clicks
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === 'add-to-game-tracker') {
    let gameTitle = '';

    // Try to get title from selection first
    if (info.selectionText) {
      gameTitle = info.selectionText.trim();
    }

    // Send message to content script to get detected title or show prompt
    chrome.tabs.sendMessage(tab.id, {
      action: 'showAddDialog',
      suggestedTitle: gameTitle
    });
  }
});

// Handle messages from content script
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'addGame') {
    addGameToTracker(request.title, request.platforms)
      .then(result => sendResponse(result))
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true; // Keep channel open for async response
  }

  if (request.action === 'searchIGDB') {
    searchIGDB(request.query)
      .then(result => sendResponse(result))
      .catch(err => sendResponse({ error: err.message }));
    return true;
  }

  if (request.action === 'getServerUrl') {
    chrome.storage.sync.get(['serverUrl'], (result) => {
      sendResponse({ serverUrl: result.serverUrl || 'http://localhost:5000' });
    });
    return true;
  }
});

async function getServerUrl() {
  return new Promise(resolve => {
    chrome.storage.sync.get(['serverUrl'], (result) => {
      resolve(result.serverUrl || 'http://localhost:5000');
    });
  });
}

async function addGameToTracker(title, platforms = []) {
  const serverUrl = await getServerUrl();

  // First search IGDB for cover art
  let coverUrl = '';
  try {
    const searchResults = await searchIGDB(title);
    if (searchResults.length > 0) {
      const exactMatch = searchResults.find(g => g.name.toLowerCase() === title.toLowerCase());
      const match = exactMatch || searchResults[0];
      coverUrl = match.cover_url || '';
    }
  } catch (e) {
    console.log('IGDB search failed, adding without cover:', e);
  }

  const gameData = { title, platforms };
  if (coverUrl) gameData.cover_url = coverUrl;

  const response = await fetch(`${serverUrl}/api/games`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(gameData)
  });

  const data = await response.json();

  if (response.ok) {
    return { success: true, gameId: data.game_id };
  } else if (response.status === 409) {
    return { success: false, exists: true, gameId: data.game_id, error: 'Game already exists' };
  } else {
    throw new Error(data.error || 'Failed to add game');
  }
}

async function searchIGDB(query) {
  const serverUrl = await getServerUrl();
  const response = await fetch(`${serverUrl}/api/igdb/search?q=${encodeURIComponent(query)}`);
  return response.json();
}

'use strict';
// Runs after dom_shim.js and the literal `_JS` text, exactly like
// assert_footer.js. `_JS` ends with markNew(); apply();, so the page has
// already been laid out and badged once by the time this file executes.
//
// Everything here goes through the real controls — the discard button's own
// click handlers, the real `apply()` — rather than calling the functions
// behind them, so what is proven is the wiring a user actually touches.

const state = {};

// ---- new since you last looked ------------------------------------------
// dom_shim seeded ms.lastViewed at 2026-07-26; svl95 was first seen before
// that, the other two after.
state.newBadges = {
  svl95: isNewBadged(cardOf('svl95')),
  svl90: isNewBadged(cardOf('svl90')),
  t770: isNewBadged(cardOf('t770')),
};
state.newBadgeOnPick = isNewBadged(pickOf('t770'));
state.newCountText = document.getElementById('newCount').textContent;
// The watermark must advance to the page's generated stamp, not the clock.
state.watermarkAfterView = storedValue('ms.lastViewed');

// ---- discarding ----------------------------------------------------------
state.trayHiddenBefore = document.getElementById('tray').hidden;
state.picksHeadBefore = document.getElementById('picksHead').textContent;

cardOf('t770').querySelector('.discard').fire('click');

state.cardHiddenAfterDiscard = cardOf('t770').hidden;
state.pickHiddenAfterDiscard = pickOf('t770').hidden;
state.picksHeadAfterDiscard = document.getElementById('picksHead').textContent;
state.otherCardStillVisible = cardOf('svl90').hidden === false;
state.trayHiddenAfterDiscard = document.getElementById('tray').hidden;
state.trayCommand = document.getElementById('trayCmd').value;
state.trayText = document.getElementById('trayText').textContent;
state.persisted = storedValue('ms.discards');

// "show discarded" must bring it back into view without un-discarding it.
document.getElementById('showDiscarded').fire('click');
state.cardVisibleWhenShowingDiscarded = cardOf('t770').hidden === false;
state.stillMarkedDiscarded = cardOf('t770').dataset.discarded === '1';
document.getElementById('showDiscarded').fire('click');

// ---- undoing -------------------------------------------------------------
cardOf('t770').querySelector('.discard').fire('click');

state.cardVisibleAfterUndo = cardOf('t770').hidden === false;
state.pickVisibleAfterUndo = pickOf('t770').hidden === false;
state.trayHiddenAfterUndo = document.getElementById('tray').hidden;
state.persistedAfterUndo = storedValue('ms.discards');

console.log(JSON.stringify(state));

// Room setup: click a room to select it, then click its neighbouring rooms.
// The underlying checkboxes ("pair") are the source of truth, so the form also works without JavaScript.
(function () {
  const tiles = Array.from(document.querySelectorAll("#room-tiles .tile"));
  const pairs = Array.from(document.querySelectorAll("input[name=pair]"));
  const hint = document.getElementById("select-hint");
  let selected = null;

  function pairBox(a, b) {
    return pairs.find(p => (p.dataset.a === a && p.dataset.b === b) || (p.dataset.a === b && p.dataset.b === a));
  }
  function neighbours(room) {
    return pairs.filter(p => p.checked && (p.dataset.a === room || p.dataset.b === room))
      .map(p => (p.dataset.a === room ? p.dataset.b : p.dataset.a));
  }
  function render() {
    tiles.forEach(tile => {
      const room = tile.dataset.room;
      const enabled = tile.querySelector("input[name=enabled]").checked;
      tile.classList.toggle("disabled", !enabled);
      tile.classList.toggle("selected", room === selected);
      tile.setAttribute("aria-pressed", room === selected ? "true" : "false");
      tile.classList.toggle("neighbor", !!selected && room !== selected && neighbours(selected).includes(room));
      tile.querySelector(".nb-list").textContent = neighbours(room).length ? "↔ " + neighbours(room).join(", ") : "";
    });
    if (selected) {
      hint.hidden = false;
      hint.textContent = window.ZPA_HINT.replace("%ROOM%", selected);
    } else {
      hint.hidden = true;
    }
  }
  function activate(tile, ev) {
    if (ev && ev.target.closest("label, input")) return; // checkbox/radio clicks keep their own meaning
    const room = tile.dataset.room;
    if (!selected || selected === room) {
      selected = selected === room ? null : room;
    } else {
      const box = pairBox(selected, room);
      if (box) box.checked = !box.checked;
    }
    render();
  }
  tiles.forEach(tile => {
    tile.addEventListener("click", ev => activate(tile, ev));
    tile.addEventListener("keydown", ev => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); activate(tile); }
      if (ev.key === "Escape") { selected = null; render(); }
    });
  });
  document.querySelectorAll("input[name=enabled], input[name=pair]").forEach(i => i.addEventListener("change", render));
  render();
})();

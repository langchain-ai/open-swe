const files = [...document.querySelectorAll("main details")];
const pressed = (button, on) => button.setAttribute("aria-pressed", String(on));
document.getElementById("expand").onclick = () =>
  files.forEach((f) => (f.open = true));
document.getElementById("collapse").onclick = () =>
  files.forEach((f) => (f.open = false));
for (const [id, cls] of [
  ["wrap", "wrap"],
  ["changes", "changes-only"],
]) {
  const button = document.getElementById(id);
  button.onclick = () => pressed(button, document.body.classList.toggle(cls));
}
document.getElementById("filter").oninput = (event) => {
  const query = event.target.value.toLowerCase();
  document
    .querySelectorAll("[data-path]")
    .forEach((el) =>
      el.classList.toggle(
        "hidden",
        !el.dataset.path.toLowerCase().includes(query),
      ),
    );
};
document.querySelectorAll("nav button").forEach(
  (button) =>
    (button.onclick = () => {
      const file = document.getElementById(button.dataset.target);
      file.open = true;
      file.scrollIntoView({ block: "start" });
    }),
);

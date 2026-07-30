export function Logo() {
  const ascii = [
"      ███                                   ███          ",
"      ░░░                                   ░░░          ",
"     █████  ██████   ████████  █████ █████ ████   █████ ",
"    ░░███  ░░░░░███ ░░███░░███░░███ ░░███ ░░███  ███░░  ",
"     ░███   ███████  ░███ ░░░  ░███  ░███  ░███ ░░█████ ",
"     ░███  ███░░███  ░███      ░░███ ███   ░███  ░░░░███",
"     ░███ ░░████████ █████      ░░█████    █████ ██████ ",
"     ░███  ░░░░░░░░ ░░░░░        ░░░░░    ░░░░░ ░░░░░░  ",
" ███ ░███                                               ",
"░░██████                                                ",
" ░░░░░░                                                 ",
  ].join("\n");

  return (
    <div className="w-full" style={{ containerType: "inline-size" }}>
      <pre
        className="m-0 w-full overflow-hidden text-center font-mono leading-none text-[#F5A623]"
        style={{ fontSize: "min(7px, 1.9cqw)" }}
      >
        {ascii}
      </pre>
    </div>
  );
}

import ast, sys

try:
    tree = ast.parse(sys.stdin.read())
except SyntaxError as exc:
    # Reported on stderr under a dedicated exit code (bound by the Rust runner)
    # so the CLI can turn it into a parse error naming the file. stdout stays
    # purely the extraction result: scripts print authored strings verbatim, so
    # an in-band marker could be produced by a real config.
    print(f"{exc.lineno or 1}:{exc.offset or 1}:{exc.msg}", file=sys.stderr)
    sys.exit(_MDA_SYNTAX_ERROR_EXIT_CODE)

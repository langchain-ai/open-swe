from managed_deepagents import basic, bearer, connections, define_sandbox

github = connections.get("github", {"type": "agent"})

sandbox = define_sandbox(
    idle_ttl_seconds=1800,
    default_timeout=300,
    run_config={"env_vars": {"GH_TOKEN": "proxy-injected"}},
    proxy_config={
        "rules": [
            {
                "name": "github-api",
                "match_hosts": ["api.github.com"],
                "headers": [{"name": "Authorization", "value": bearer(github)}],
            },
            {
                "name": "github-git",
                "match_hosts": ["github.com"],
                "headers": [{"name": "Authorization", "value": basic("x-access-token", github)}],
            },
        ],
    },
)

use std::cell::Cell;
use std::io::{BufRead, BufReader, Read, Write};

use crate::langsmith::agent_server::pending_interrupts;
use std::net::TcpListener;
use std::sync::mpsc::{self, Receiver};
use std::thread;
use std::time::{Duration, Instant};

use super::*;

const THREAD: &str = "11111111-1111-4111-8111-111111111111";

fn server(
    responses: Vec<(u16, String)>,
) -> (
    AgentServer,
    Receiver<(String, Value)>,
    thread::JoinHandle<()>,
) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let (sender, receiver) = mpsc::channel();
    let handle = thread::spawn(move || {
        for (status, body) in responses {
            let deadline = Instant::now() + Duration::from_secs(5);
            let mut stream = loop {
                match listener.accept() {
                    Ok((stream, _)) => break stream,
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                        assert!(Instant::now() < deadline, "missing HTTP request");
                        thread::sleep(Duration::from_millis(5));
                    }
                    Err(error) => panic!("failed to accept request: {error}"),
                }
            };
            // macOS inherits the listener's nonblocking mode on accepted sockets.
            stream.set_nonblocking(false).unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            stream
                .set_write_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut reader = BufReader::new(&mut stream);
            let mut request = String::new();
            reader.read_line(&mut request).unwrap();
            let mut length = 0;
            loop {
                let mut line = String::new();
                assert!(
                    reader.read_line(&mut line).unwrap() > 0,
                    "truncated request"
                );
                if line == "\r\n" {
                    break;
                }
                if let Some((name, value)) = line.split_once(':') {
                    if name.eq_ignore_ascii_case("content-length") {
                        length = value.trim().parse().unwrap();
                    }
                }
            }
            assert!(length <= 1024 * 1024, "unexpected request size");
            let mut payload = vec![0; length];
            reader.read_exact(&mut payload).unwrap();
            sender
                .send((
                    request.trim().to_string(),
                    if payload.is_empty() {
                        Value::Null
                    } else {
                        serde_json::from_slice(&payload).unwrap()
                    },
                ))
                .unwrap();
            write!(
                stream,
                "HTTP/1.1 {status} Result\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        }
    });
    (
        AgentServer::new(url, "test-agent".into()).unwrap(),
        receiver,
        handle,
    )
}

fn pending_state() -> Value {
    json!({"values": {"messages": []}, "tasks": [{"interrupts": [
        {"id": "11111111111111111111111111111111", "value": "Review this action"}
    ]}]})
}

#[test]
fn run_stream_exposes_typed_messages_and_current_state() {
    use crate::langsmith::agent_server::{Message, RunEvent, RunState, ToolCall};

    let previous = json!({"messages": [{"id": "old", "type": "ai", "content": "old reply"}]});
    let values = json!({"messages": [
        previous["messages"][0],
        {"id": "new", "type": "ai", "content": [{"type": "text", "text": "new reply"}]}
    ], "__interrupt__": [{"id": "review", "value": "Continue?"}]});
    let stream = format!(
        "event: metadata\ndata: {{}}\n\nevent: messages\ndata: [{{\"type\":\"AIMessageChunk\",\"content\":\"new\",\"tool_call_chunks\":[{{\"id\":\"call\",\"name\":\"read\"}}]}},{{}}]\n\nevent: messages\ndata: [{{\"type\":\"tool\",\"name\":\"read\",\"content\":\"result\"}},{{}}]\n\nevent: values\ndata: {values}\n\n"
    );
    let (server, _requests, handle) = server(vec![
        (200, json!({"values": previous}).to_string()),
        (200, stream),
    ]);
    let events = server
        .stream_run(THREAD, json!({"input": {}}))
        .unwrap()
        .collect::<Result<Vec<_>>>()
        .unwrap();
    handle.join().unwrap();
    assert_eq!(
        events,
        vec![
            RunEvent::Ignored,
            RunEvent::Message(Message::Assistant {
                text: "new".into(),
                tool_calls: vec![ToolCall {
                    key: "call".into(),
                    name: "read".into()
                }],
            }),
            RunEvent::Message(Message::Tool {
                name: "read".into(),
                text: "result".into()
            }),
            RunEvent::Values(RunState {
                reply: Some("new reply".into()),
                interrupts: vec![PendingInterrupt {
                    id: Some("review".into()),
                    value: json!("Continue?"),
                    response_schema: None,
                }],
            }),
        ]
    );
}

#[test]
fn approval_note_cannot_send_terminal_control_commands() {
    let note = tool_review_note(&json!({"name": "read\u{1b}[2J", "args": {}}));
    assert!(!note.contains("\u{1b}[2J"));
}

#[test]
fn approval_menu_only_offers_allowed_decisions() {
    let request = json!({"review_configs": [
        {"action_name": "send", "allowed_decisions": ["approve"]},
        {"action_name": "remove", "allowed_decisions": ["reject", "edit"]}
    ]});
    assert_eq!(review_choices(&request, "send").unwrap(), ["approve"]);
    assert_eq!(review_choices(&request, "remove").unwrap(), ["reject"]);
}

#[test]
fn approval_menu_refuses_unknown_or_unsupported_policies() {
    for request in [
        json!({}),
        json!({"review_configs": []}),
        json!({"review_configs": [{"action_name": "send", "allowed_decisions": ["edit"]}]}),
        json!({"review_configs": [{"action_name": "send", "allowed_decisions": []}]}),
        json!({"review_configs": [{"action_name": "send", "allowed_decisions": "approve"}]}),
    ] {
        assert!(
            review_choices(&request, "send").is_err(),
            "accepted {request}"
        );
    }
}

/// A per-call `response_schema` as Python publishes it: a `oneOf` of `$ref`s.
fn python_schema() -> Value {
    json!({
        "oneOf": [
            {"$ref": "#/$defs/ApproveDecision"},
            {"$ref": "#/$defs/EditDecision"},
            {"$ref": "#/$defs/RejectDecision"}
        ],
        "$defs": {
            "ApproveDecision": {"properties": {"type": {"const": "approve"}}},
            "EditDecision": {"properties": {"type": {"const": "edit"}}},
            "RejectDecision": {"properties": {"type": {"const": "reject"}}}
        }
    })
}

#[test]
fn per_call_menu_follows_the_response_schema() {
    assert_eq!(
        decision_choices(&python_schema()),
        ["approve", "edit", "reject"]
    );
    let inline = json!({"anyOf": [
        {"properties": {"type": {"const": "reject"}}},
        {"properties": {"type": {"const": "approve"}}}
    ]});
    assert_eq!(decision_choices(&inline), ["approve", "reject"]);
    let one = json!({"properties": {"type": {"const": "respond"}}});
    assert_eq!(decision_choices(&one), ["respond"]);
    // Only per-call values get this menu; batched and free-form ones keep theirs.
    assert!(is_tool_approval(&json!({"type": "tool_approval"})));
    assert!(!is_tool_approval(&json!({"action_requests": []})));
    assert!(!is_tool_approval(&json!("Continue?")));
}

#[test]
fn resume_command_preserves_project_and_resolved_connection() {
    let options = ChatOptions {
        path: "./my agent".into(),
        url: None,
        assistant: None,
        thread: None,
    };
    let target = Target::Pinned {
        api_url: "http://localhost:3030".into(),
    };
    let server = AgentServer::new("http://localhost:3030".into(), "other agent".into()).unwrap();
    let command = resume_command(&options, &target, &server, THREAD);
    assert!(command.contains("'./my agent'"), "{command}");
    assert!(
        command.contains("--url 'http://localhost:3030'"),
        "{command}"
    );
    assert!(command.contains("--assistant 'other agent'"), "{command}");
    assert!(command.contains(THREAD));
}

/// A server this session started is gone once it ends, so pinning its URL
/// would only send the next run at a dead port instead of starting a new one.
#[test]
fn resume_command_omits_a_connection_the_user_did_not_pin() {
    let server = AgentServer::new("http://localhost:2024".into(), "agent".into()).unwrap();
    let attached = Target::Attached {
        api_url: "http://localhost:2024".into(),
    };
    let command = resume_command(&opts(), &attached, &server, THREAD);
    assert!(command.starts_with("mda chat --assistant "), "{command}");
    assert!(!command.contains("--url"), "{command}");
}

#[test]
fn adopting_a_thread_does_not_create_it() {
    let (server, requests, handle) = server(vec![(200, json!({"thread_id": THREAD}).to_string())]);
    server.ensure_thread(Some(THREAD)).unwrap();
    handle.join().unwrap();
    assert_eq!(
        requests.recv().unwrap(),
        (format!("GET /threads/{THREAD} HTTP/1.1"), Value::Null)
    );
}

#[test]
fn pending_approval_is_resumed_before_accepting_a_message() {
    let (server, requests, handle) = server(vec![
        (200, pending_state().to_string()),
        (200, pending_state().to_string()),
        (
            200,
            "event: values\ndata: {\"messages\":[]}\n\nevent: end\ndata: null\n\n".into(),
        ),
    ]);
    let reviewed = Cell::new(false);
    let mut thread = THREAD.to_string();
    let result = chat_loop(
        &server,
        &mut thread,
        &Interrupt::pending(),
        || {
            assert!(
                reviewed.get(),
                "requested input before resolving pending approval"
            );
            Ok(None)
        },
        |pending| {
            assert_eq!(pending.len(), 1);
            assert_eq!(pending[0].value, json!("Review this action"));
            reviewed.set(true);
            Ok(Some(json!("approved")))
        },
        |message| panic!("unexpected run failure: {message}"),
    )
    .unwrap();
    assert_eq!(result, CommandCompletion::Succeeded);
    handle.join().unwrap();
    assert_eq!(
        requests.recv().unwrap().0,
        format!("GET /threads/{THREAD}/state HTTP/1.1")
    );
    assert_eq!(
        requests.recv().unwrap().0,
        format!("GET /threads/{THREAD}/state HTTP/1.1")
    );
    let (path, body) = requests.recv().unwrap();
    assert_eq!(path, format!("POST /threads/{THREAD}/runs/stream HTTP/1.1"));
    assert_eq!(body["command"], json!({"resume": "approved"}));
    assert!(body.get("input").is_none());
}

#[test]
fn cancelling_recovered_approval_does_not_start_a_run() {
    let (server, requests, handle) = server(vec![(200, pending_state().to_string())]);
    let mut thread = THREAD.to_string();
    let result = chat_loop(
        &server,
        &mut thread,
        &Interrupt::pending(),
        || panic!("must not request new input"),
        |_| Ok(None),
        |message| panic!("unexpected run failure: {message}"),
    )
    .unwrap();
    assert_eq!(result, CommandCompletion::Cancelled);
    handle.join().unwrap();
    assert_eq!(requests.try_iter().count(), 1);
}

fn opts() -> ChatOptions {
    ChatOptions {
        path: PathBuf::from("."),
        url: None,
        assistant: None,
        thread: None,
    }
}

#[test]
fn api_url_is_trimmed_and_must_be_http() {
    assert_eq!(
        normalize_api_url(" https://agent.example.com/ ").unwrap(),
        "https://agent.example.com"
    );
    assert!(normalize_api_url("agent.example.com").is_err());
}

#[test]
fn assistant_id_prefers_flag_then_staged_env() {
    let mut staged = BTreeMap::new();
    staged.insert(MDA_ASSISTANT_ID_ENV.to_string(), "staged-agent".to_string());
    let mut with_flag = opts();
    with_flag.assistant = Some(" flag-agent ".into());
    assert_eq!(
        resolve_assistant_id(&with_flag, &staged).unwrap(),
        "flag-agent"
    );
    assert_eq!(
        resolve_assistant_id(&opts(), &staged).unwrap(),
        "staged-agent"
    );
}

#[test]
fn resume_payload_unwraps_a_single_answer_and_keys_several() {
    assert_eq!(
        resume_payload(vec![(Some("a".into()), json!("x"))]).unwrap(),
        json!("x")
    );
    assert_eq!(
        resume_payload(vec![
            (Some("a".into()), json!(1)),
            (Some("b".into()), json!(2))
        ])
        .unwrap(),
        json!({"a": 1, "b": 2})
    );
}

#[test]
fn ambiguous_interrupt_answers_are_rejected() {
    for answers in [
        vec![(Some("a".into()), json!(1)), (None, json!(2))],
        vec![(Some("a".into()), json!(1)), (Some("a".into()), json!(2))],
    ] {
        assert!(resume_payload(answers).is_err());
    }
}

#[test]
fn keyed_interrupt_answers_retain_the_server_keys() {
    let pending = pending_interrupts(&json!({"__interrupt__": {
        "task-a": [{"value": "first"}], "task-b": [{"value": "second"}]
    }}));
    let answers = pending.into_iter().map(|p| (p.id, p.value)).collect();
    assert_eq!(
        resume_payload(answers).unwrap(),
        json!({"task-a": "first", "task-b": "second"})
    );
}

#[test]
fn typescript_and_python_tool_reviews_have_the_same_choices() {
    let typescript = json!({
        "actionRequests": [{"name": "send", "args": {"to": "test"}}],
        "reviewConfigs": [{"actionName": "send", "allowedDecisions": ["approve", "edit"]}]
    });
    let python = json!({
        "action_requests": [{"name": "send", "args": {"to": "test"}}],
        "review_configs": [{"action_name": "send", "allowed_decisions": ["approve", "edit"]}]
    });
    assert_eq!(
        tool_actions(&typescript).unwrap(),
        tool_actions(&python).unwrap()
    );
    assert_eq!(review_choices(&typescript, "send").unwrap(), ["approve"]);
    assert_eq!(
        review_choices(&typescript, "send").unwrap(),
        review_choices(&python, "send").unwrap()
    );
    assert!(tool_actions(&json!("A free-form question"))
        .unwrap()
        .is_none());
    assert!(tool_actions(&json!({"actionRequests": "malformed"})).is_err());
}

#[test]
fn resume_command_quotes_shell_metacharacters_and_leading_dash_path() {
    let dangerous = "someone's $(command); `command`";
    let quoted = if cfg!(windows) {
        "'someone''s $(command); `command`'"
    } else {
        "'someone'\\''s $(command); `command`'"
    };
    assert_eq!(shell_quote(dangerous), quoted);
    let options = ChatOptions {
        path: "-agent".into(),
        ..opts()
    };
    let server = AgentServer::new("http://localhost:3030".into(), dangerous.into()).unwrap();
    let target = Target::Attached {
        api_url: "http://localhost:3030".into(),
    };
    let command = resume_command(&options, &target, &server, THREAD);
    assert!(command.contains(&format!("--assistant {quoted}")));
    assert!(command.ends_with(" -- '-agent'"));
}

#[test]
fn missing_thread_is_not_recreated() {
    let (server, requests, handle) = server(vec![(
        404,
        json!({"detail": "Thread not found"}).to_string(),
    )]);
    assert!(server.ensure_thread(Some(THREAD)).is_err());
    handle.join().unwrap();
    let requests: Vec<_> = requests.try_iter().collect();
    assert_eq!(
        requests,
        [(format!("GET /threads/{THREAD} HTTP/1.1"), Value::Null)]
    );
}

#[test]
fn state_read_failure_does_not_accept_input_or_start_a_run() {
    let (server, requests, handle) =
        server(vec![(500, json!({"detail": "Unavailable"}).to_string())]);
    let result = chat_loop(
        &server,
        &mut THREAD.to_string(),
        &Interrupt::pending(),
        || panic!("state must be loaded before input"),
        |_| panic!("invalid state must not prompt for review"),
        |message| panic!("unexpected run failure: {message}"),
    );
    assert!(result.is_err());
    handle.join().unwrap();
    assert_eq!(requests.try_iter().count(), 1);
}

#[test]
fn fresh_turn_can_interrupt_then_resume_on_the_same_thread() {
    let (server, requests, handle) = server(vec![
        (200, json!({"values": null, "tasks": []}).to_string()),
        (200, json!({"values": {"messages": []}}).to_string()),
        (200, "event: values\ndata: {\"__interrupt__\":[{\"id\":\"review\",\"value\":\"Proceed?\"}]}\n\n".into()),
        (
            200,
            json!({"values": {"messages": [], "__interrupt__": [{"id": "review", "value": "Proceed?"}]}}).to_string(),
        ),
        (200, pending_state().to_string()),
        (200, "event: values\ndata: {\"messages\":[]}\n\nevent: end\ndata: null\n\n".into()),
    ]);
    let reviewed = Cell::new(false);
    let mut messages = [Some("hello".to_string()), None].into_iter();
    let result = chat_loop(
        &server,
        &mut THREAD.to_string(),
        &Interrupt::pending(),
        || {
            let message = messages.next().unwrap();
            if message.is_none() {
                assert!(reviewed.get());
            }
            Ok(message)
        },
        |pending| {
            assert_eq!(pending[0].value, json!("Proceed?"));
            reviewed.set(true);
            Ok(Some(json!("yes")))
        },
        |message| panic!("unexpected run failure: {message}"),
    )
    .unwrap();
    assert_eq!(result, CommandCompletion::Succeeded);
    handle.join().unwrap();
    let requests: Vec<_> = requests.try_iter().collect();
    assert_eq!(requests.len(), 6);
    assert_eq!(
        requests[2].0,
        format!("POST /threads/{THREAD}/runs/stream HTTP/1.1")
    );
    assert_eq!(
        requests[2].1["input"],
        json!({"messages": [{"role": "human", "content": "hello"}]})
    );
    assert_eq!(requests[5].0, requests[2].0);
    assert_eq!(requests[5].1["command"], json!({"resume": "yes"}));
    assert_eq!(requests[5].1["assistant_id"], json!("test-agent"));
    assert_eq!(
        requests[5].1["stream_mode"],
        json!(["messages-tuple", "values"])
    );
}

#[test]
fn concurrent_interrupts_send_answers_under_the_original_keys() {
    let state = json!({"values": {"messages": [], "__interrupt__": {
        "task-a": [{"value": "first"}],
        "task-b": [{"id": "interrupt-b", "value": "second"}]
    }}});
    let (server, requests, handle) = server(vec![
        (200, state.to_string()),
        (200, state.to_string()),
        (200, "event: values\ndata: {\"messages\":[]}\n\n".into()),
    ]);
    chat_loop(
        &server,
        &mut THREAD.to_string(),
        &Interrupt::pending(),
        || Ok(None),
        |pending| {
            Ok(Some(resume_payload(
                pending
                    .iter()
                    .map(|p| (p.id.clone(), p.value.clone()))
                    .collect(),
            )?))
        },
        |message| panic!("unexpected run failure: {message}"),
    )
    .unwrap();
    handle.join().unwrap();
    let requests: Vec<_> = requests.try_iter().collect();
    assert_eq!(requests.len(), 3);
    assert_eq!(
        requests[2].1["command"]["resume"],
        json!({"task-a": "first", "interrupt-b": "second"})
    );
}

#[test]
fn failed_pre_run_snapshot_does_not_start_a_run() {
    let (server, requests, handle) =
        server(vec![(500, json!({"detail": "Unavailable"}).to_string())]);
    let error =
        stream_turn(&server, THREAD, json!({"input": {}}), &Interrupt::pending()).unwrap_err();
    assert!(format!("{error:#}").contains("Unavailable"));
    assert_eq!(crate::error::category_of(&error), ErrorCategory::Remote);
    handle.join().unwrap();
    let requests: Vec<_> = requests.try_iter().collect();
    assert_eq!(
        requests,
        [(format!("GET /threads/{THREAD}/state HTTP/1.1"), Value::Null)]
    );
}

#[test]
fn failed_run_is_reported_and_the_session_continues() {
    let error = json!({
        "error": "Error",
        "message": "Missing credentials. Please pass an `apiKey`, or set the `OPENAI_API_KEY` environment variable."
    });
    let (server, requests, handle) = server(vec![
        (200, json!({"values": null, "tasks": []}).to_string()),
        (200, json!({"values": {"messages": []}}).to_string()),
        (200, format!("event: error\ndata: {error}\n\n")),
        (
            200,
            json!({"values": {"messages": []}, "tasks": []}).to_string(),
        ),
    ]);
    let mut failures = Vec::new();
    let mut messages = [Some("hello".to_string()), None].into_iter();

    let result = chat_loop(
        &server,
        &mut THREAD.to_string(),
        &Interrupt::pending(),
        || Ok(messages.next().unwrap()),
        |_| panic!("nothing to review"),
        |message| {
            failures.push(message.to_string());
            Ok(())
        },
    )
    .unwrap();

    handle.join().unwrap();
    assert_eq!(result, CommandCompletion::Succeeded);
    assert_eq!(
        failures,
        ["Missing credentials. Please pass an `apiKey`, or set the `OPENAI_API_KEY` environment variable."]
    );
    assert_eq!(requests.try_iter().count(), 4);
}

#[test]
fn run_error_message_keeps_plain_text_errors() {
    assert_eq!(run_error_message("boom\u{1b}[2J"), "boom[2J");
}

#[test]
fn run_error_message_names_the_type_when_the_message_is_redacted() {
    let raw =
        json!({"error": "OpenAIAuthenticationError", "message": "An internal error occurred"});

    assert_eq!(
        run_error_message(&raw.to_string()),
        "OpenAIAuthenticationError: An internal error occurred"
    );
}

#[test]
fn run_failure_hint_names_the_key_the_failure_mentions() {
    let hint = run_failure_hint(
        "set the `OPENAI_API_KEY` environment variable",
        None,
        Path::new("/work/my-agent/.env"),
        "and `mda dev` reloads it on save.",
    );

    assert_eq!(
        hint.as_deref(),
        Some("Add `OPENAI_API_KEY=...` to /work/my-agent/.env, and `mda dev` reloads it on save.")
    );
}

#[test]
fn run_failure_hint_points_an_authentication_failure_at_the_model_key() {
    let hint = run_failure_hint(
        "OpenAIAuthenticationError: An internal error occurred",
        Some("OPENAI_API_KEY"),
        Path::new(".env"),
        "then restart `mda chat`.",
    );

    assert_eq!(
        hint.as_deref(),
        Some("Set a valid `OPENAI_API_KEY` in .env, then restart `mda chat`.")
    );
}

#[test]
fn run_failure_hint_ignores_unrelated_failures() {
    assert_eq!(
        run_failure_hint(
            "MY_OPENAI_API_KEY_FILE is unreadable",
            Some("OPENAI_API_KEY"),
            Path::new(".env"),
            ""
        ),
        None
    );
}

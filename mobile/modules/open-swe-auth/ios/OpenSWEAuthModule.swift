import ExpoModulesCore
import Foundation
import Network

/// The two sign-in steps JavaScript cannot do itself:
///
/// - Listening on `127.0.0.1` for the dashboard's desktop-handoff redirect,
///   `GET /callback?code=…`. Every code that arrives is sent as `onCode`; the
///   JavaScript side decides which one redeems.
/// - Local dev sign-in: `GET /auth/dev-login` answers with the session cookie on
///   a redirect, which `fetch` would follow and never show.
public class OpenSWEAuthModule: Module {
  private var listener: LoopbackListener?

  public func definition() -> ModuleDefinition {
    Name("OpenSWEAuth")

    Events("onCode")

    /// Start listening and resolve with the port, always 1024 or above.
    AsyncFunction("startLoopback") { () async throws -> Int in
      self.listener?.stop()
      let listener = LoopbackListener { [weak self] code in
        self?.sendEvent("onCode", ["code": code])
      }
      self.listener = listener
      return Int(try await listener.start())
    }

    Function("stopLoopback") {
      self.listener?.stop()
      self.listener = nil
    }

    /// The session token from a backend running under `langgraph dev`, or nil
    /// when it does not offer dev sign-in. JavaScript checks the backend is on
    /// this machine before calling.
    AsyncFunction("devSignIn") { (backend: URL) async throws -> String? in
      try await DevSignIn.signIn(backend: backend)
    }
  }
}

enum DevSignIn {
  static func signIn(backend: URL) async throws -> String? {
    var request = URLRequest(url: backend.appendingPathComponent("dashboard/api/auth/dev-login"))
    request.httpShouldHandleCookies = false
    let session = URLSession(configuration: .ephemeral, delegate: RefuseRedirects(), delegateQueue: nil)
    defer { session.finishTasksAndInvalidate() }
    let (data, response) = try await session.data(for: request)
    guard let http = response as? HTTPURLResponse else { return nil }
    if http.statusCode == 404 { return nil }
    let headers = http.allHeaderFields.reduce(into: [String: String]()) { result, field in
      if let key = field.key as? String, let value = field.value as? String { result[key] = value }
    }
    let cookie = HTTPCookie.cookies(withResponseHeaderFields: headers, for: request.url!)
      .first { $0.name == "osw_session" }
    guard let token = cookie?.value, !token.isEmpty else {
      let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
      let detail = json?["detail"] as? String ?? "Local sign-in did not return a session."
      throw Exception(name: "DevSignInFailed", description: "HTTP \(http.statusCode): \(detail)")
    }
    return token
  }

  /// The sign-in answers with a redirect to the web dashboard; the cookie on
  /// that redirect is all the app needs.
  private final class RefuseRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(
      _ session: URLSession,
      task: URLSessionTask,
      willPerformHTTPRedirection response: HTTPURLResponse,
      newRequest request: URLRequest
    ) async -> URLRequest? {
      nil
    }
  }
}

/// An HTTP listener on 127.0.0.1 that reports every `GET /callback?code=…`.
/// It keeps serving until stopped: whether a code signs in is settled by the
/// exchange, so a stray request cannot end a real sign-in.
final class LoopbackListener: @unchecked Sendable {
  private let onCode: (String) -> Void
  private var listener: NWListener?
  private let queue = DispatchQueue(label: "dev.langchain.openswe.loopback")

  init(onCode: @escaping (String) -> Void) {
    self.onCode = onCode
  }

  func start() async throws -> UInt16 {
    let parameters = NWParameters.tcp
    parameters.requiredLocalEndpoint = .hostPort(host: .ipv4(.loopback), port: .any)
    parameters.allowLocalEndpointReuse = true
    let listener = try NWListener(using: parameters)
    self.listener = listener
    listener.newConnectionHandler = { [weak self] connection in self?.serve(connection) }
    return try await withCheckedThrowingContinuation { continuation in
      let once = ResumeOnce(continuation)
      listener.stateUpdateHandler = { state in
        switch state {
        case .ready:
          if let port = listener.port?.rawValue, port >= 1024 {
            once.resume(with: .success(port))
          } else {
            once.resume(with: .failure(Exception(name: "LoopbackFailed", description: "No usable port")))
          }
        case .failed(let error):
          once.resume(with: .failure(error))
        default:
          break
        }
      }
      listener.start(queue: queue)
    }
  }

  func stop() {
    listener?.cancel()
    listener = nil
  }

  private func serve(_ connection: NWConnection) {
    connection.start(queue: queue)
    connection.receive(minimumIncompleteLength: 1, maximumLength: 16_384) { [weak self] data, _, _, _ in
      let request = data.map { String(decoding: $0, as: UTF8.self) } ?? ""
      let target = request.split(separator: " ").dropFirst().first.map(String.init) ?? ""
      let components = URLComponents(string: "http://127.0.0.1\(target)")
      guard components?.path == "/callback" else {
        Self.respond(connection, status: "404 Not Found", body: "")
        return
      }
      guard let code = components?.queryItems?.first(where: { $0.name == "code" })?.value, !code.isEmpty else {
        Self.respond(connection, status: "400 Bad Request", body: Self.page("Sign-in failed", "Robin did not receive a sign-in code."))
        return
      }
      Self.respond(connection, status: "200 OK", body: Self.page("Almost done", "Return to the Robin app to finish signing in."))
      self?.onCode(code)
    }
  }

  private static func respond(_ connection: NWConnection, status: String, body: String) {
    let payload = "HTTP/1.1 \(status)\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n\(body)"
    connection.send(content: Data(payload.utf8), completion: .contentProcessed { _ in connection.cancel() })
  }

  private static func page(_ heading: String, _ detail: String) -> String {
    """
    <!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">\
    <title>Robin</title><style>:root{color-scheme:light dark}body{font:17px -apple-system,sans-serif;\
    display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:90vh;text-align:center}\
    p{opacity:.7}</style></head><body><h2>\(heading)</h2><p>\(detail)</p></body></html>
    """
  }
}

/// Resumes a continuation at most once, whichever side finishes first.
private final class ResumeOnce<T>: @unchecked Sendable {
  private var continuation: CheckedContinuation<T, Error>?
  private let lock = NSLock()

  init(_ continuation: CheckedContinuation<T, Error>) {
    self.continuation = continuation
  }

  func resume(with result: Result<T, Error>) {
    lock.lock()
    let pending = continuation
    continuation = nil
    lock.unlock()
    pending?.resume(with: result)
  }
}

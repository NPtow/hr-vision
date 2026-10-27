// Build: swiftc -parse-as-library scripts/yandex-diagnose.swift -o .build/yandex-diagnose
// Default is offline. --execute makes exactly one identical native-app search.
// Credentials stay in process memory and are fetched through Security.framework.
import Foundation
import Security

private struct Credentials: Decodable { let apiKey: String; let folderID: String }
private final class NoRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}
private func report(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
       let string = String(data: data, encoding: .utf8) { print(string) }
}
private func sanitized(_ text: String, credentials: Credentials) -> String {
    var text = text
    for secret in [credentials.apiKey, credentials.folderID] where !secret.isEmpty {
        text = text.replacingOccurrences(of: secret, with: "[REDACTED]")
    }
    for pattern in [#"(?i)(?:Api-Key|Bearer)\s+\S+"#,
                    #"AQVN[A-Za-z0-9_-]{20,}"#,
                    #"(?i)(?:authorization|api[_-]?key|token|secret)\s*[=:]\s*[^\s,;]+"#] {
        text = text.replacingOccurrences(of: pattern, with: "[REDACTED]", options: .regularExpression)
    }
    return String(text.unicodeScalars.filter { !CharacterSet.controlCharacters.contains($0) || $0 == " " }.map(String.init).joined().prefix(1200))
}

@main private enum Diagnose {
    static func main() async {
        let args = Array(CommandLine.arguments.dropFirst())
        if args == ["--self-test"] {
            let fixture = Credentials(apiKey: "test-key-for-redaction", folderID: "test-folder")
            let value = sanitized("Folder test-folder key test-key-for-redaction Api-Key other-secret authorization=other-secret", credentials: fixture)
            precondition(!value.contains(fixture.apiKey) && !value.contains(fixture.folderID) && !value.contains("other-secret"))
            report(["selfTest": "passed", "networkRequests": 0, "keychainReads": 0]); return
        }
        guard args == ["--execute"] else {
            report(["mode": "offline", "networkRequests": 0, "keychainReads": 0,
                    "execution": "Pass --execute for one quoted Бруноям Search API request; no retries."])
            return
        }
        var item: CFTypeRef?
        let status = SecItemCopyMatching([
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "ru.hrvision.tgsignals.sources",
            kSecAttrAccount as String: "yandex",
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ] as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data,
              let credentials = try? JSONDecoder().decode(Credentials.self, from: data),
              !credentials.apiKey.isEmpty, !credentials.folderID.isEmpty,
              credentials.apiKey.utf8.allSatisfy({ $0 >= 33 && $0 <= 126 }) else {
            report(["error": "keychain-or-credential-unavailable", "securityStatus": status]); return
        }
        let config = URLSessionConfiguration.ephemeral
        config.urlCache = nil; config.httpCookieStorage = nil; config.urlCredentialStorage = nil
        config.httpShouldSetCookies = false; config.httpAdditionalHeaders = nil
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 30; config.timeoutIntervalForResource = 45
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        var request = URLRequest(url: URL(string: "https://searchapi.api.cloud.yandex.net/v2/web/search")!,
                                 cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 30)
        request.httpMethod = "POST"; request.httpShouldHandleCookies = false
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("Api-Key " + credentials.apiKey, forHTTPHeaderField: "Authorization")
        request.httpBody = try? JSONSerialization.data(withJSONObject: [
            "folderId": credentials.folderID,
            "query": ["searchType": "SEARCH_TYPE_RU", "queryText": "\"Бруноям\"", "page": "0"],
            "responseFormat": "FORMAT_XML",
            "groupSpec": ["groupMode": "GROUP_MODE_FLAT", "groupsOnPage": "10", "docsInGroup": "1"]
        ])
        do {
            let (bytes, response) = try await session.bytes(for: request, delegate: NoRedirects())
            guard let http = response as? HTTPURLResponse else { report(["error": "non-http-response"]); return }
            if (200..<300).contains(http.statusCode) {
                report(["httpStatus": http.statusCode, "message": "Request succeeded; response content intentionally omitted."]); return
            }
            var body = Data()
            for try await byte in bytes {
                guard body.count < 32768 else {
                    report(["httpStatus": http.statusCode, "message": "Error body exceeded 32768 bytes; omitted."]); return
                }
                body.append(byte)
            }
            var output: [String: Any] = ["httpStatus": http.statusCode]
            if let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any] {
                let error = (object["error"] as? [String: Any]) ?? object
                if let message = error["message"] as? String { output["message"] = sanitized(message, credentials: credentials) }
                if let code = error["code"] as? Int { output["serviceCode"] = code }
                else if let code = error["code"] as? String { output["serviceCode"] = sanitized(code, credentials: credentials) }
                if output["message"] == nil { output["message"] = "JSON error has no message; body omitted." }
            } else { output["message"] = "Non-JSON error; body omitted." }
            report(output)
        } catch {
            // NSError / URLError descriptions may include request details; never print them.
            report(["error": "transport-failure", "urlErrorCode": (error as? URLError)?.errorCode ?? 0])
        }
    }
}

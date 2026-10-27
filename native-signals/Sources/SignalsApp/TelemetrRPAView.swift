import SwiftUI
import SignalsCore

struct TelemetrRPAView: View {
    @EnvironmentObject private var workspace: SignalsWorkspace
    @ObservedObject var model: TelemetrRPAModel
    @ObservedObject var collection: TelemetrCollectionModel
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 10) {
                    Label("Сбор в открытом браузере", systemImage: "globe").font(.title2.weight(.semibold))
                    Text("Codex использует текущий вход в Telemetr: открывает выдачу, листает страницы и сохраняет публикации сюда. Ключ API не нужен.")
                        .foregroundStyle(.secondary)
                    Text("Этот режим работает во время работы с Codex. Результаты доступны во вкладке «Результаты» и остаются на этом Mac.")
                        .font(.callout).foregroundStyle(.secondary)
                    ForEach(model.browserJobs) { job in
                        VStack(alignment: .leading, spacing: 8) {
                            Label(job.message, systemImage: job.state == "complete" ? "checkmark.circle" : "arrow.triangle.2.circlepath")
                                .font(.headline)
                            ProgressView(value: Double(job.finishedQueries), total: Double(max(1, job.totalQueries)))
                            Text("\(job.finishedQueries)/\(job.totalQueries) компаний · \(job.pages) страниц · \(job.posts) публикаций")
                                .font(.callout).foregroundStyle(.secondary)
                        }.padding(16).background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 10))
                    }
                }
            }.padding(24)
        }
        .task {
            while !Task.isCancelled {
                await model.refresh(collection: collection)
                do { try await Task.sleep(for: .seconds(3)) } catch { break }
            }
        }
    }
}

import AppKit
import Foundation

let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
let formats = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)]
for (points, scale) in formats {
    let pixels = points * scale
    let image = NSImage(size: NSSize(width: pixels, height: pixels))
    image.lockFocus()
    let factor = CGFloat(pixels) / 1024
    let transform = AffineTransform(scale: factor)
    (transform as NSAffineTransform).concat()
    let background = NSBezierPath(roundedRect: NSRect(x: 60, y: 60, width: 904, height: 904), xRadius: 210, yRadius: 210)
    NSGradient(starting: NSColor(red: 0.28, green: 0.42, blue: 0.86, alpha: 1), ending: NSColor(red: 0.14, green: 0.22, blue: 0.59, alpha: 1))!.draw(in: background, angle: 90)
    let a = NSPoint(x: 305, y: 704), b = NSPoint(x: 305, y: 330), c = NSPoint(x: 727, y: 515)
    let lines = NSBezierPath()
    lines.move(to: a); lines.line(to: c); lines.line(to: b)
    lines.lineWidth = 40; lines.lineCapStyle = .round
    NSColor.white.withAlphaComponent(0.65).setStroke(); lines.stroke()
    for (point, radius) in [(a, CGFloat(72)), (b, 72), (c, 104)] {
        let circle = NSBezierPath(ovalIn: NSRect(x: point.x-radius, y: point.y-radius, width: radius*2, height: radius*2))
        NSColor.white.setFill(); circle.fill()
    }
    image.unlockFocus()
    let rep = NSBitmapImageRep(data: image.tiffRepresentation!)!
    let suffix = scale == 2 ? "@2x" : ""
    try rep.representation(using: .png, properties: [:])!.write(to: root.appendingPathComponent("icon_\(points)x\(points)\(suffix).png"))
}

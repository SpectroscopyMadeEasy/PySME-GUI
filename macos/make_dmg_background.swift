// Draw the background of the installer window: an arrow from the app to the
// Applications folder, and one line of instruction.
// Usage: swift make_dmg_background.swift OUTPUT_DIRECTORY
// Writes background.png and background@2x.png. The sizes and the icon positions
// must match dmg_settings.py.

import Cocoa

let arguments = CommandLine.arguments
guard arguments.count == 2 else {
    FileHandle.standardError.write(Data("usage: make_dmg_background.swift OUTPUT_DIRECTORY\n".utf8))
    exit(1)
}
let output = URL(fileURLWithPath: arguments[1])
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)

let width: CGFloat = 640, height: CGFloat = 400
let iconRow: CGFloat = 175                  // centre of the icons, from the top

func render(scale: CGFloat) -> Data {
    let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: Int(width * scale), pixelsHigh: Int(height * scale),
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    rep.size = NSSize(width: width, height: height)   // draw in points
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

    // A light background, so that Finder's dark file names stay readable.
    NSColor(red: 0.965, green: 0.961, blue: 0.949, alpha: 1).setFill()
    NSRect(x: 0, y: 0, width: width, height: height).fill()

    // Only a hint at the direction: thin, short and grey, so that the two icons
    // stay the things one looks at.
    let y = height - iconRow                // Cocoa counts from the bottom
    let arrow = NSBezierPath()
    arrow.move(to: NSPoint(x: 296, y: y))
    arrow.line(to: NSPoint(x: 343, y: y))
    arrow.move(to: NSPoint(x: 334, y: y + 8))
    arrow.line(to: NSPoint(x: 343, y: y))
    arrow.line(to: NSPoint(x: 334, y: y - 8))
    arrow.lineWidth = 2.5
    arrow.lineCapStyle = .round
    arrow.lineJoinStyle = .round
    NSColor(white: 0.70, alpha: 1).setStroke()
    arrow.stroke()

    let style = NSMutableParagraphStyle()
    style.alignment = .center
    ("Drag PySME onto Applications to install it" as NSString).draw(
        in: NSRect(x: 0, y: height - 318, width: width, height: 22),
        withAttributes: [
            .font: NSFont.systemFont(ofSize: 14),
            .foregroundColor: NSColor(white: 0.45, alpha: 1),
            .paragraphStyle: style,
        ])

    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

try render(scale: 1).write(to: output.appendingPathComponent("background.png"))
try render(scale: 2).write(to: output.appendingPathComponent("background@2x.png"))

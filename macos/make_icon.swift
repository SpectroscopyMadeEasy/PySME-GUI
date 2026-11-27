// Draw the app icon: the emblem from the PySME logo on a rounded tile.
// Usage: swift make_icon.swift LOGO.png OUTPUT.iconset

import Cocoa

let arguments = CommandLine.arguments
guard arguments.count == 3,
      let image = NSImage(contentsOfFile: arguments[1]),
      let full = image.cgImage(forProposedRect: nil, context: nil, hints: nil)
else {
    FileHandle.standardError.write(Data("usage: make_icon.swift LOGO.png OUTPUT.iconset\n".utf8))
    exit(1)
}

// The emblem sits above the lettering; these fractions frame it.
let width = CGFloat(full.width), height = CGFloat(full.height)
let crop = CGRect(x: width * 0.333, y: height * 0.070, width: width * 0.343, height: height * 0.465)
guard let emblem = full.cropping(to: crop) else { exit(1) }

let output = URL(fileURLWithPath: arguments[2])
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)

func render(_ pixels: Int) -> Data {
    let size = CGFloat(pixels)
    let context = CGContext(data: nil, width: pixels, height: pixels, bitsPerComponent: 8,
                            bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    let tile = CGRect(x: size * 0.098, y: size * 0.098, width: size * 0.804, height: size * 0.804)
    context.addPath(CGPath(roundedRect: tile, cornerWidth: size * 0.18, cornerHeight: size * 0.18, transform: nil))
    context.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
    context.fillPath()

    // The logo as it is: its emblem, in its own black, on white, about as tall
    // in the tile as the glyphs of other apps.
    // Check any other size as the system shows it (NSWorkspace icon of the built
    // app): macOS 26 draws some sizes grey and soft, e.g. 0.62, 0.648 and 0.70.
    let box = size * 0.682
    let ratio = CGFloat(emblem.width) / CGFloat(emblem.height)
    context.interpolationQuality = .high
    context.draw(emblem, in: CGRect(x: (size - box * ratio) / 2, y: (size - box) / 2,
                                    width: box * ratio, height: box))

    let rep = NSBitmapImageRep(cgImage: context.makeImage()!)
    return rep.representation(using: .png, properties: [:])!
}

for base in [16, 32, 128, 256, 512] {
    try render(base).write(to: output.appendingPathComponent("icon_\(base)x\(base).png"))
    try render(base * 2).write(to: output.appendingPathComponent("icon_\(base)x\(base)@2x.png"))
}

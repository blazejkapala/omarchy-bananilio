.pragma library

// Local estimate of unmodified SMS text. No network or automatic transliteration.
var basic = "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
var extended = "\f^{}\\[~]|€"

function estimate(text, from, to) {
    var chars = Array.from(text), unicode = [], units = 0
    chars.forEach(function(c) {
        if (basic.indexOf(c) < 0 && extended.indexOf(c) < 0 && unicode.indexOf(c) < 0) unicode.push(c)
    })
    var gsm = unicode.length === 0
    var sizes = chars.map(function(c) { return gsm ? (extended.indexOf(c) >= 0 ? 2 : 1) : c.length })
    sizes.forEach(function(n) { units += n })
    var single = gsm ? 160 : 70
    // Conservative estimate for NANP destinations when using a toll-free sender.
    var tollFree = /^\+1(?:800|833|844|855|866|877|888)\d{7}$/.test(from || "") && /^\+1/.test(to || "")
    var capacity = units <= single ? single : gsm ? (tollFree ? 152 : 153) : (tollFree ? 66 : 67)
    var segments = text.length ? 1 : 0, used = 0
    sizes.forEach(function(size) {
        // Extension escapes and UTF-16 surrogate pairs stay in the same segment.
        if (used + size > capacity) { segments++; used = 0 }
        used += size
    })
    return {encoding: gsm ? "GSM-7" : "Unicode", units: units, segments: segments,
            remaining: capacity - used, capacity: capacity, unicode: unicode,
            tollFree: tollFree, characters: chars.length}
}

function summary(info) {
    return info.segments + " SMS · " + info.encoding + " · " + info.remaining + " units remaining"
}

function unicodeDetails(info) {
    return info.unicode.slice(0, 12).map(function(c) {
        var code = c.codePointAt(0).toString(16).toUpperCase()
        while (code.length < 4) code = "0" + code
        return (c.trim() ? "“" + c + "” " : "") + "U+" + code
    }).join(", ") + (info.unicode.length > 12 ? ", …" : "")
}

function withoutPolish(text) {
    var original = "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", replacement = "acelnoszzACELNOSZZ"
    var result = text
    for (var i = 0; i < original.length; i++) {
        // Also handle decomposed Polish letters, without changing other text.
        var letter = original[i], decomposed = letter.normalize("NFD")
        if (decomposed !== letter) result = result.split(decomposed).join(replacement[i])
        result = result.split(letter).join(replacement[i])
    }
    return result
}

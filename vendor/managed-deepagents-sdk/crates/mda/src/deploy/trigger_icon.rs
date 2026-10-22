use std::io::Write;

use anyhow::{Context, Result};
use crc32fast::Hasher;
use flate2::write::ZlibEncoder;
use flate2::Compression;

const IMAGE_SIZE: usize = 512;
const GRID_SIZE: usize = 6;
const CELL_SIZE: usize = 53;
const DOT_SIZE: usize = 43;
const GLYPH_SIZE: usize = GRID_SIZE * CELL_SIZE;
const PADDING: usize = (IMAGE_SIZE - GLYPH_SIZE) / 2;
const RGB_CHANNELS: usize = 3;
const LC_PALETTE: [IconColors; 5] = [
    IconColors {
        foreground: [0x7f, 0xc8, 0xff],
        background: [0x07, 0x16, 0x22],
    },
    IconColors {
        foreground: [0xe3, 0xff, 0x8f],
        background: [0x1b, 0x22, 0x07],
    },
    IconColors {
        foreground: [0xb2, 0x7d, 0x75],
        background: [0x22, 0x0b, 0x07],
    },
    IconColors {
        foreground: [0xc7, 0x8e, 0xad],
        background: [0x22, 0x07, 0x16],
    },
    IconColors {
        foreground: [0xd5, 0xc3, 0xf7],
        background: [0x10, 0x07, 0x22],
    },
];

struct IconColors {
    foreground: [u8; RGB_CHANNELS],
    background: [u8; RGB_CHANNELS],
}

struct IconModel {
    pattern: [bool; GRID_SIZE * GRID_SIZE / 2],
    fold_rows: bool,
    foreground: [u8; RGB_CHANNELS],
    background: [u8; RGB_CHANNELS],
}

#[derive(Debug, PartialEq)]
pub(super) struct GeneratedIcon {
    pub(super) png: Vec<u8>,
    pub(super) background_color: String,
}

pub(super) fn generate(name: &str) -> Result<GeneratedIcon> {
    let model = icon_model(name);
    Ok(GeneratedIcon {
        png: encode_png(&render(&model))?,
        background_color: hex_color(model.background),
    })
}

fn render(model: &IconModel) -> Vec<u8> {
    let mut pixels = model.background.repeat(IMAGE_SIZE * IMAGE_SIZE);

    for row in 0..GRID_SIZE {
        for column in 0..GRID_SIZE {
            let model_index = mirrored_index(row, column, model.fold_rows);
            if !model.pattern[model_index] {
                continue;
            }
            paint_dot(&mut pixels, row, column, model.foreground);
        }
    }

    pixels
}

fn icon_model(name: &str) -> IconModel {
    let mut random = XorShift32::new(fnv1a(name));
    let mut pattern = std::array::from_fn(|_| random.next_bool());
    let mut fold_rows = random.next_bool();

    for _ in 1..64 {
        if pattern_touches_all_edges(&pattern, fold_rows) {
            break;
        }
        pattern = std::array::from_fn(|_| random.next_bool());
        fold_rows = random.next_bool();
    }
    if !pattern_touches_all_edges(&pattern, fold_rows) {
        pattern.fill(true);
    }

    let colors = &LC_PALETTE[(random.next() as usize) % LC_PALETTE.len()];

    IconModel {
        pattern,
        fold_rows,
        foreground: colors.foreground,
        background: colors.background,
    }
}

fn pattern_touches_all_edges(pattern: &[bool; GRID_SIZE * GRID_SIZE / 2], fold_rows: bool) -> bool {
    let top = (0..GRID_SIZE).any(|column| pattern[mirrored_index(0, column, fold_rows)]);
    let bottom =
        (0..GRID_SIZE).any(|column| pattern[mirrored_index(GRID_SIZE - 1, column, fold_rows)]);
    let left = (0..GRID_SIZE).any(|row| pattern[mirrored_index(row, 0, fold_rows)]);
    let right = (0..GRID_SIZE).any(|row| pattern[mirrored_index(row, GRID_SIZE - 1, fold_rows)]);

    top && bottom && left && right
}

fn mirrored_index(row: usize, column: usize, fold_rows: bool) -> usize {
    if fold_rows {
        row.min(GRID_SIZE - 1 - row) * GRID_SIZE + column
    } else {
        row * (GRID_SIZE / 2) + column.min(GRID_SIZE - 1 - column)
    }
}

fn paint_dot(pixels: &mut [u8], row: usize, column: usize, color: [u8; RGB_CHANNELS]) {
    let inset = (CELL_SIZE - DOT_SIZE) / 2;
    let left = PADDING + column * CELL_SIZE + inset;
    let top = PADDING + row * CELL_SIZE + inset;

    for dot_y in 0..DOT_SIZE {
        let delta_y = (2 * dot_y + 1).abs_diff(DOT_SIZE);
        for dot_x in 0..DOT_SIZE {
            let delta_x = (2 * dot_x + 1).abs_diff(DOT_SIZE);
            if delta_x * delta_x + delta_y * delta_y > DOT_SIZE * DOT_SIZE {
                continue;
            }

            let pixel_x = left + dot_x;
            let pixel_y = top + dot_y;
            let offset = (pixel_y * IMAGE_SIZE + pixel_x) * RGB_CHANNELS;
            pixels[offset..offset + RGB_CHANNELS].copy_from_slice(&color);
        }
    }
}

fn fnv1a(name: &str) -> u32 {
    name.encode_utf16().fold(0x811c_9dc5, |hash, code_unit| {
        (hash ^ u32::from(code_unit)).wrapping_mul(0x0100_0193)
    })
}

struct XorShift32(u32);

impl XorShift32 {
    fn new(seed: u32) -> Self {
        Self(if seed == 0 { 0x9e37_79b9 } else { seed })
    }

    fn next(&mut self) -> u32 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 17;
        self.0 ^= self.0 << 5;
        self.0
    }

    fn next_bool(&mut self) -> bool {
        self.next() < 0x8000_0000
    }
}

fn hex_color([red, green, blue]: [u8; RGB_CHANNELS]) -> String {
    format!("#{red:02x}{green:02x}{blue:02x}")
}

#[allow(
    unknown_lints,
    clippy::chunks_exact_to_as_chunks,
    reason = "slice::as_chunks requires Rust 1.88; mda supports Rust 1.85"
)]
fn encode_png(pixels: &[u8]) -> Result<Vec<u8>> {
    let mut scanlines = Vec::with_capacity(IMAGE_SIZE * (IMAGE_SIZE * RGB_CHANNELS + 1));
    for row in pixels.chunks_exact(IMAGE_SIZE * RGB_CHANNELS) {
        scanlines.push(0);
        scanlines.extend_from_slice(row);
    }

    let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
    encoder
        .write_all(&scanlines)
        .context("failed to encode generated channel icon")?;
    let compressed = encoder
        .finish()
        .context("failed to finish generated channel icon")?;

    let mut png = Vec::with_capacity(compressed.len() + 57);
    png.extend_from_slice(b"\x89PNG\r\n\x1a\n");
    let mut header = [0_u8; 13];
    header[..4].copy_from_slice(&(IMAGE_SIZE as u32).to_be_bytes());
    header[4..8].copy_from_slice(&(IMAGE_SIZE as u32).to_be_bytes());
    header[8..].copy_from_slice(&[8, 2, 0, 0, 0]);
    write_chunk(&mut png, b"IHDR", &header)?;
    write_chunk(&mut png, b"IDAT", &compressed)?;
    write_chunk(&mut png, b"IEND", &[])?;
    Ok(png)
}

fn write_chunk(png: &mut Vec<u8>, kind: &[u8; 4], data: &[u8]) -> Result<()> {
    let length = u32::try_from(data.len()).context("generated channel icon is too large")?;
    png.extend_from_slice(&length.to_be_bytes());
    png.extend_from_slice(kind);
    png.extend_from_slice(data);

    let mut crc = Hasher::new();
    crc.update(kind);
    crc.update(data);
    png.extend_from_slice(&crc.finalize().to_be_bytes());
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::collections::HashSet;

    use super::*;

    const EXPECTED_PALETTE: [([u8; RGB_CHANNELS], [u8; RGB_CHANNELS]); 5] = [
        ([0x7f, 0xc8, 0xff], [0x07, 0x16, 0x22]),
        ([0xe3, 0xff, 0x8f], [0x1b, 0x22, 0x07]),
        ([0xb2, 0x7d, 0x75], [0x22, 0x0b, 0x07]),
        ([0xc7, 0x8e, 0xad], [0x22, 0x07, 0x16]),
        ([0xd5, 0xc3, 0xf7], [0x10, 0x07, 0x22]),
    ];

    #[test]
    fn generate_returns_a_512_pixel_rgb_png() {
        let icon = generate("Support Bot").unwrap();

        assert_eq!(
            icon.png.get(..26),
            Some(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR\0\0\x02\0\0\0\x02\0\x08\x02".as_slice())
        );
    }

    #[test]
    fn generate_is_stable_for_the_same_name() {
        assert_eq!(
            generate("Support Bot").unwrap(),
            generate("Support Bot").unwrap()
        );
    }

    #[test]
    fn generated_background_matches_the_rendered_background() {
        let model = icon_model("Support Bot");
        let pixels = render(&model);
        let icon = generate("Support Bot").unwrap();

        assert_eq!(icon.background_color, hex_color(pixel(&pixels, 0, 0)));
    }

    #[test]
    fn icon_model_uses_every_lc_color_pair() {
        let actual: HashSet<_> = (0..100)
            .map(|index| {
                let model = icon_model(&format!("Slack Bot {index}"));
                (model.foreground, model.background)
            })
            .collect();
        let expected = HashSet::from(EXPECTED_PALETTE);

        assert_eq!(actual, expected);
    }

    #[test]
    fn render_keeps_the_outer_padding_at_the_background_color() {
        let model = icon_model("Support Bot");
        let pixels = render(&model);

        let glyph = PADDING..PADDING + GLYPH_SIZE;
        let padding_matches_background = (0..IMAGE_SIZE).all(|y| {
            (0..IMAGE_SIZE).all(|x| {
                let outside_glyph = !glyph.contains(&x) || !glyph.contains(&y);
                !outside_glyph || pixel(&pixels, x, y) == model.background
            })
        });

        assert!(padding_matches_background);
    }

    #[test]
    fn render_uses_only_the_selected_palette_colors() {
        let model = icon_model("Support Bot");
        let pixels = render(&model);
        let actual: HashSet<_> = pixels
            .chunks(RGB_CHANNELS)
            .map(|pixel| [pixel[0], pixel[1], pixel[2]])
            .collect();
        let expected = HashSet::from([model.background, model.foreground]);

        assert_eq!(actual, expected);
    }

    #[test]
    fn render_paints_occupied_cells_as_solid_circles() {
        let mut model = icon_model("Support Bot");
        model.pattern = [false; GRID_SIZE * GRID_SIZE / 2];
        model.pattern[0] = true;
        let pixels = render(&model);

        let left = PADDING + 5;
        let top = PADDING + 5;
        let center = PADDING + CELL_SIZE / 2;

        assert_eq!(
            (
                pixel(&pixels, left, top),
                pixel(&pixels, center, top),
                pixel(&pixels, center, center),
            ),
            (model.background, model.foreground, model.foreground)
        );
    }

    #[test]
    fn render_uses_original_visible_outer_padding() {
        let mut model = icon_model("Support Bot");
        model.pattern = [true; GRID_SIZE * GRID_SIZE / 2];
        let pixels = render(&model);

        assert_eq!(
            (pixel(&pixels, 101, 123), pixel(&pixels, 102, 123)),
            (model.background, model.foreground)
        );
    }

    #[test]
    fn glyph_uses_a_six_by_six_grid() {
        assert_eq!(
            (GRID_SIZE, CELL_SIZE, DOT_SIZE, GLYPH_SIZE, PADDING),
            (6, 53, 43, 318, 97)
        );
    }

    #[test]
    fn generated_patterns_touch_all_four_grid_edges() {
        for index in 0..1_000 {
            let model = icon_model(&format!("Slack Bot {index}"));

            assert!(
                pattern_touches_all_edges(&model.pattern, model.fold_rows),
                "pattern for Slack Bot {index} does not touch every edge"
            );
        }
    }

    #[test]
    fn render_paints_a_lighter_glyph() {
        let model = icon_model("Support Bot");
        let pixels = render(&model);
        let background_brightness: u16 = model.background.map(u16::from).into_iter().sum();

        let has_lighter_pixel = (0..IMAGE_SIZE).any(|y| {
            (0..IMAGE_SIZE).any(|x| {
                pixel(&pixels, x, y).map(u16::from).into_iter().sum::<u16>() > background_brightness
            })
        });

        assert!(has_lighter_pixel);
    }

    fn pixel(pixels: &[u8], x: usize, y: usize) -> [u8; RGB_CHANNELS] {
        let offset = (y * IMAGE_SIZE + x) * RGB_CHANNELS;
        [pixels[offset], pixels[offset + 1], pixels[offset + 2]]
    }
}

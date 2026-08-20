export type StyleCategory = "材质幻化" | "手作工艺" | "玩具微缩" | "绘画印刷" | "自然实验" | "未来视觉";

export type StylePreset = {
  id: string;
  label: string;
  category: StyleCategory;
  summary: string;
  swatch: string;
  prompt: string;
  avoid: string;
  strength: number;
};

const preset = (value: StylePreset) => value;

/**
 * These are prompt recipes rather than display filters. Each recipe names a
 * medium/material, its physical response and the imperfections that make it
 * legible to image models.
 */
export const STYLE_PRESETS: StylePreset[] = [
  preset({ id: "blown-glass", label: "吹制玻璃", category: "材质幻化", summary: "透明厚壁、折射焦散", swatch: "linear-gradient(135deg,#dffcff,#5ac5dd 55%,#f8fdff)", prompt: "blown-glass construction, transparent thick walls, rounded edges, realistic refraction, internal reflections, transmitted highlights, subtle trapped air bubbles and colored light caustics", avoid: "opaque plastic, flat painted surface, broken silhouette, duplicated parts", strength: 72 }),
  preset({ id: "frosted-glass", label: "磨砂玻璃", category: "材质幻化", summary: "半透明、柔雾散射", swatch: "linear-gradient(135deg,#f4f7f8,#aebfc6)", prompt: "frosted translucent glass, satin micro-rough surface, soft subsurface transmission, blurred internal forms, restrained edge highlights and believable glass thickness", avoid: "clear plastic, mirror chrome, sharp transparent interior, noisy texture", strength: 68 }),
  preset({ id: "molten-lava", label: "熔岩核心", category: "材质幻化", summary: "黑曜外壳、发光裂隙", swatch: "linear-gradient(135deg,#16100f,#ff4d12 55%,#ffc247)", prompt: "cooled black volcanic crust split by glowing molten-lava fissures, incandescent orange core, heat bloom, rough basalt edges, localized smoke and physically plausible emitted light", avoid: "uniform orange paint, random flames, melted anatomy, excessive smoke", strength: 82 }),
  preset({ id: "liquid-chrome", label: "液态铬", category: "材质幻化", summary: "镜面流体、高光拉伸", swatch: "linear-gradient(135deg,#20252d,#f5fbff 45%,#68717c)", prompt: "liquid chrome surface, mirrorlike metallic reflectance, smooth flowing curvature, stretched environment reflections, crisp specular highlights and high surface tension", avoid: "matte gray plastic, rust, noisy reflections, deformed composition", strength: 76 }),
  preset({ id: "porcelain", label: "白瓷青釉", category: "材质幻化", summary: "瓷胎、釉面细裂", swatch: "linear-gradient(135deg,#f7f4e9,#b8e1dc 65%,#347c87)", prompt: "fine white porcelain body with pale celadon glaze, controlled glossy reflections, delicate crackle lines, slightly translucent thin edges and hand-fired tonal variation", avoid: "plastic sheen, heavy cracks, dirty surface, changed proportions", strength: 66 }),
  preset({ id: "amber", label: "琥珀封存", category: "材质幻化", summary: "蜂蜜透光、气泡包裹", swatch: "linear-gradient(135deg,#5a2605,#f3a719 55%,#ffd47c)", prompt: "warm honey amber, translucent resin depth, suspended micro-bubbles and tiny inclusions, rounded polished surface, rich internal scattering and golden transmitted light", avoid: "flat yellow color, cloudy plastic, extra objects, opaque center", strength: 70 }),
  preset({ id: "ice-crystal", label: "冰晶雕塑", category: "材质幻化", summary: "冰裂、冷雾、折射", swatch: "linear-gradient(135deg,#effdff,#81d7f1 55%,#d4f8ff)", prompt: "carved crystalline ice, realistic refractive volume, fine internal stress fractures, frosted cut edges, cold condensation and faint contact meltwater", avoid: "snow-covered blob, blue plastic, shattered form, excessive fog", strength: 74 }),
  preset({ id: "jade", label: "温润玉石", category: "材质幻化", summary: "半透矿脉、蜡质光泽", swatch: "linear-gradient(135deg,#194e3c,#89c7a5 55%,#e4f1c9)", prompt: "carved nephrite jade, dense semi-translucent body, waxy luster, subtle cloudy mineral veins, softly transmitted rim light and polished hand-worn edges", avoid: "emerald glass, neon green, marble cracks, metallic shine", strength: 64 }),

  preset({ id: "hand-clay", label: "手捏黏土", category: "手作工艺", summary: "指纹、软边、定格动画", swatch: "linear-gradient(135deg,#c96f55,#f1bc8c)", prompt: "hand-built modeling clay, soft rounded forms, visible fingerprints and tool marks, small seam irregularities, matte pliable surface, handcrafted stop-motion miniature character", avoid: "smooth CGI plastic, photoreal skin, melted features, extra fingers", strength: 78 }),
  preset({ id: "knitted-wool", label: "针织毛线", category: "手作工艺", summary: "线圈结构、柔软绒毛", swatch: "linear-gradient(135deg,#dc6a84,#f7d0a0 55%,#8ab4d9)", prompt: "constructed from knitted wool, clearly readable interlocking loops following the form, soft yarn fibers, ribbed edges, realistic stitch tension and gentle fabric compression", avoid: "printed knit pattern, hard plastic, loose unreadable threads, furry blob", strength: 80 }),
  preset({ id: "crochet", label: "钩针玩偶", category: "手作工艺", summary: "粗线针目、玩偶结构", swatch: "linear-gradient(135deg,#c7654e,#f6c86a 52%,#70a684)", prompt: "amigurumi crochet construction, chunky spiral stitches, stuffed volume, neat yarn joins, buttonlike details and soft handcrafted imperfections", avoid: "flat textile print, realistic flesh, tangled yarn, missing silhouette", strength: 82 }),
  preset({ id: "needle-felt", label: "羊毛毡", category: "手作工艺", summary: "短绒纤维、针毡塑形", swatch: "linear-gradient(135deg,#f0d9c4,#be876b)", prompt: "needle-felted wool sculpture, dense short fibers, softly compacted volumes, tiny needle-punched irregularities, warm matte surface and handmade miniature finish", avoid: "long animal fur, polished plastic, fuzzy outline loss, flat felt sheet", strength: 76 }),
  preset({ id: "embroidery", label: "立体刺绣", category: "手作工艺", summary: "丝线针脚、布面浮雕", swatch: "linear-gradient(135deg,#183c48,#d85863 58%,#e9be70)", prompt: "raised hand embroidery on woven fabric, directional satin stitches describing volume, visible thread sheen, layered thread relief, tiny knots and clean fabric tension", avoid: "printed illustration, random threads, illegible face, flat vector fill", strength: 74 }),
  preset({ id: "paper-cut", label: "层叠剪纸", category: "手作工艺", summary: "纸层阴影、切边纹理", swatch: "linear-gradient(135deg,#112340,#e86973 55%,#f1d7a4)", prompt: "layered paper-cut artwork, multiple precisely cut cardstock planes, visible paper fibers on edges, shallow cast shadows between layers and tactile handcrafted depth", avoid: "single flat silhouette, glossy plastic, torn messy edges, deep 3D space", strength: 72 }),
  preset({ id: "origami", label: "折纸构造", category: "手作工艺", summary: "几何折痕、纸张张力", swatch: "linear-gradient(135deg,#faf4df,#ee715e 60%,#9db4c8)", prompt: "origami paper construction, intentional folded planes, crisp creases, believable paper thickness, controlled geometric simplification and subtle fiber texture", avoid: "crumpled paper, rounded clay, impossible folds, changed pose", strength: 72 }),
  preset({ id: "raku", label: "乐烧陶艺", category: "手作工艺", summary: "烟熏釉裂、手工陶土", swatch: "linear-gradient(135deg,#161719,#5d7064 55%,#c7a562)", prompt: "hand-fired raku ceramic, smoky matte clay, irregular metallic glaze patches, fine craquelure, kiln variation and grounded pottery weight", avoid: "perfect factory ceramic, random large cracks, chrome mirror, broken geometry", strength: 70 }),

  preset({ id: "toy-bricks", label: "积木玩具", category: "玩具微缩", summary: "ABS 拼插颗粒、模块化", swatch: "linear-gradient(135deg,#e9352f,#f7ce35 50%,#2574cf)", prompt: "rebuilt from interlocking ABS toy bricks, readable studs and modular seams, injection-molded plastic sheen, simplified block geometry and coherent brick scale", avoid: "brand logos, random floating bricks, fused studs, unchanged realistic material", strength: 84 }),
  preset({ id: "vinyl-figure", label: "潮玩公仔", category: "玩具微缩", summary: "大头比例、乙烯哑光", swatch: "linear-gradient(135deg,#6b4ec1,#ff84a9 55%,#74d8db)", prompt: "designer vinyl collectible figure, simplified expressive proportions, smooth matte vinyl, clean molded seams, compact pedestal-ready silhouette and subtle studio reflections", avoid: "real human skin, porcelain, excessive articulation, manufacturer logo", strength: 78 }),
  preset({ id: "plush-toy", label: "毛绒玩偶", category: "玩具微缩", summary: "短绒布、填充缝线", swatch: "linear-gradient(135deg,#9d744e,#ead1a8)", prompt: "soft plush-toy construction, short-pile fabric, rounded stuffed volume, visible but neat panel seams, embroidered details and gentle compression at contact points", avoid: "real fur, plastic face, loose stuffing, erased silhouette", strength: 78 }),
  preset({ id: "mini-diorama", label: "微缩模型", category: "玩具微缩", summary: "比例模型、景深尺度", swatch: "linear-gradient(135deg,#31494b,#d09b5c 58%,#dce8c0)", prompt: "handmade miniature diorama, coherent small scale, modelmaking materials, tiny crafted props, subtle adhesive and paint imperfections, macro depth cues without changing the composition", avoid: "full-size realism, oversized props, extreme blur, toy clutter", strength: 70 }),
  preset({ id: "paper-model", label: "纸模型", category: "玩具微缩", summary: "折片、插舌、印刷纸板", swatch: "linear-gradient(135deg,#e6ddc9,#7397b8 58%,#d96656)", prompt: "assembled printed paper model, folded tabs and scored edges, matte cardstock, simplified faceted volumes, precise joins and slight handmade alignment variation", avoid: "origami-only folds, plastic gloss, torn edges, flat poster", strength: 72 }),
  preset({ id: "inflatable", label: "充气软雕塑", category: "玩具微缩", summary: "软体鼓胀、热压接缝", swatch: "linear-gradient(135deg,#ff6d8b,#7de2e7 55%,#ffe569)", prompt: "inflatable soft sculpture, taut air-filled volume, heat-welded seams, gentle bulging between joins, soft vinyl highlights and slight contact deformation", avoid: "hard plastic, balloon animal knots, collapsed body, mirror finish", strength: 76 }),
  preset({ id: "wind-up-tin", label: "发条铁皮玩具", category: "玩具微缩", summary: "冲压铁皮、复古印刷", swatch: "linear-gradient(135deg,#24455b,#df5a45 55%,#e5c56b)", prompt: "vintage wind-up tin toy, stamped sheet-metal construction, lithographed color, folded tabs, tiny rivets, restrained scratches and a visible mechanical key", avoid: "heavy rust, modern plastic, extra mechanisms, destroyed likeness", strength: 74 }),
  preset({ id: "capsule-toy", label: "扭蛋模型", category: "玩具微缩", summary: "透明胶囊、迷你摆件", swatch: "linear-gradient(135deg,#edfaff,#7fc9ed 48%,#f1789e)", prompt: "compact capsule-toy miniature, clean molded plastic parts, charming simplified proportions, glossy transparent shell accents and believable mass-produced seams", avoid: "text labels, human realism, giant capsule, random accessories", strength: 72 }),

  preset({ id: "impasto", label: "厚涂油画", category: "绘画印刷", summary: "刮刀笔触、颜料堆叠", swatch: "linear-gradient(135deg,#153b6d,#e2a437 52%,#cf533b)", prompt: "impasto oil painting, thick directional palette-knife strokes that follow form, layered wet-on-dry pigment, raised paint ridges and selective canvas grain", avoid: "smooth digital airbrush, chaotic strokes, flat vector shapes, lost facial structure", strength: 76 }),
  preset({ id: "gouache", label: "不透明水粉", category: "绘画印刷", summary: "平涂层次、干刷边缘", swatch: "linear-gradient(135deg,#536fa7,#e8907c 55%,#f0d5a7)", prompt: "opaque gouache illustration, matte layered color blocks, controlled dry-brush edges, subtle paper tooth and simplified but accurate value grouping", avoid: "transparent watercolor wash, glossy paint, muddy colors, random outlines", strength: 68 }),
  preset({ id: "ink-wash", label: "水墨晕染", category: "绘画印刷", summary: "墨色层次、宣纸留白", swatch: "linear-gradient(135deg,#15191c,#778083 55%,#ece8dc)", prompt: "ink-wash painting on absorbent paper, graded ink density, controlled bleeding edges, dry-brush texture, deliberate negative space and restrained mineral color accents", avoid: "comic outlines, uniform grayscale filter, excessive splatter, erased composition", strength: 70 }),
  preset({ id: "linocut", label: "亚麻油毡版画", category: "绘画印刷", summary: "粗粝刀痕、强对比墨色", swatch: "linear-gradient(135deg,#111,#eee 52%,#b7352f)", prompt: "linocut relief print, bold carved marks describing planes, high-contrast ink coverage, visible gouge texture, slight registration and ink-pressure variation", avoid: "smooth vector art, tiny photographic detail, gray blur, random crosshatch", strength: 78 }),
  preset({ id: "risograph", label: "孔版印刷", category: "绘画印刷", summary: "双色套印、颗粒错位", swatch: "linear-gradient(135deg,#ff4b78,#2358d8 58%,#f6e56c)", prompt: "two- or three-ink risograph print, limited spot-color palette, visible halftone grain, slight color misregistration and uncoated paper texture", avoid: "full-spectrum gradient, glossy photo, perfect registration, dense black mud", strength: 72 }),
  preset({ id: "cyanotype", label: "蓝晒摄影", category: "绘画印刷", summary: "普鲁士蓝、日晒轮廓", swatch: "linear-gradient(135deg,#061c55,#1459a5 55%,#e7f0df)", prompt: "cyanotype contact print, deep Prussian blue field, pale sun-exposed silhouettes, uneven sensitizer brush edges and fibrous watercolor paper", avoid: "ordinary blue tint, neon cyan, glossy surface, lost subject edges", strength: 76 }),
  preset({ id: "stained-glass", label: "彩色玻璃窗", category: "绘画印刷", summary: "铅条分区、彩玻透光", swatch: "linear-gradient(135deg,#173177,#d84263 45%,#f1c146 72%,#2f806e)", prompt: "stained-glass window design, colored translucent glass pieces separated by coherent dark lead cames, transmitted jewel-toned light, hand-cut edge variation and small glass bubbles", avoid: "mosaic tiles, painted black lines, random fragmentation, opaque colors", strength: 82 }),
  preset({ id: "mosaic", label: "马赛克镶嵌", category: "绘画印刷", summary: "石片拼合、砂浆缝隙", swatch: "linear-gradient(135deg,#2d6d7a,#ddb65c 55%,#b54c3a)", prompt: "hand-laid mosaic made of small stone and glass tesserae, readable grout channels, directional tile placement following form, varied chips and mineral reflectance", avoid: "pixel art, seamless photo texture, random tile size, broken contour", strength: 78 }),

  preset({ id: "moss-grown", label: "苔藓共生", category: "自然实验", summary: "湿润苔绒、自然侵覆", swatch: "linear-gradient(135deg,#19381e,#6f9b45 55%,#c5cf76)", prompt: "partially overgrown with dense living moss, fine damp filaments, small lichen colonies, believable growth in shaded creases, retained underlying structure and scattered dew", avoid: "uniform green fur, jungle clutter, hidden silhouette, random flowers", strength: 68 }),
  preset({ id: "mycelium", label: "菌丝结构", category: "自然实验", summary: "白色菌网、有机生长", swatch: "linear-gradient(135deg,#342d2a,#e7dfc8 58%,#c59b69)", prompt: "grown from branching mycelium, dense pale fungal fibers, organic cellular bridges, porous matte surface and controlled fruiting details along structural seams", avoid: "gore, moldy decay, random mushrooms, collapsed anatomy", strength: 74 }),
  preset({ id: "coral", label: "珊瑚骨架", category: "自然实验", summary: "多孔分枝、海洋矿物", swatch: "linear-gradient(135deg,#e36667,#f1b99e 52%,#6bc3c6)", prompt: "coral-mineral construction, porous calcareous surface, branching growth that follows the original form, subtle marine color variation and wet subsurface scattering", avoid: "underwater clutter, random fish, sponge blob, destroyed silhouette", strength: 74 }),
  preset({ id: "salt-crystal", label: "盐晶析出", category: "自然实验", summary: "晶簇、生长边缘", swatch: "linear-gradient(135deg,#f7f5ed,#c7def0 58%,#dfb9d5)", prompt: "encrusted with geometric salt crystals, translucent cubic growth concentrated on edges, fine powder residue, varied crystal scale and crisp mineral sparkle", avoid: "snow, glitter coating, uniform spikes, concealed details", strength: 68 }),
  preset({ id: "oxidized-copper", label: "铜锈侵蚀", category: "自然实验", summary: "铜绿层次、边缘露铜", swatch: "linear-gradient(135deg,#9b542e,#3b9a87 58%,#b8d4b7)", prompt: "aged copper with layered turquoise verdigris, exposed warm metal on handled edges, pitted oxidation, matte mineral bloom and selective metallic reflections", avoid: "uniform teal paint, heavy destruction, orange rust, unreadable detail", strength: 66 }),
  preset({ id: "charred-wood", label: "炭化木", category: "自然实验", summary: "烧杉纹理、黑亮木裂", swatch: "linear-gradient(135deg,#080a09,#302c27 58%,#9a6543)", prompt: "charred timber surface, deep blackened wood grain, controlled alligator cracking, satin carbon highlights and warm raw wood visible at selected edges", avoid: "active fire, ash cloud, random holes, flat black fill", strength: 68 }),
  preset({ id: "pressed-flowers", label: "压花标本", category: "自然实验", summary: "薄叶花瓣、植物拼贴", swatch: "linear-gradient(135deg,#efe4c7,#b96e78 52%,#6c8460)", prompt: "assembled from pressed botanical specimens, flattened translucent petals and leaves, delicate veins, archival paper fibers and carefully layered herbarium collage", avoid: "fresh 3D bouquet, random foliage, thick shadows, obscured face", strength: 72 }),
  preset({ id: "soap-bubble", label: "肥皂泡膜", category: "自然实验", summary: "虹彩薄膜、透明张力", swatch: "linear-gradient(135deg,#8de2e5,#ef9ddd 48%,#f7ed91)", prompt: "ultrathin soap-film membrane, transparent curved surfaces, spectral iridescence, delicate interference bands, high surface tension and crisp luminous rims", avoid: "solid rainbow plastic, foam clusters, burst form, muddy colors", strength: 74 }),

  preset({ id: "holographic", label: "全息镭射", category: "未来视觉", summary: "衍射虹彩、金属薄膜", swatch: "linear-gradient(135deg,#66e3ec,#9d7ef1 46%,#f4a7cf 72%,#e8f277)", prompt: "holographic diffraction foil, angle-dependent spectral highlights, fine micro-groove shimmer, cool metallic base and controlled rainbow separation on curved edges", avoid: "rainbow gradient paint, blown highlights, noisy glitter, flat surface", strength: 70 }),
  preset({ id: "translucent-resin", label: "半透树脂", category: "未来视觉", summary: "浇注层、内部悬浮物", swatch: "linear-gradient(135deg,#3e7ac1,#9e8ee8 52%,#f0b1cb)", prompt: "cast translucent resin, polished rounded edges, layered color depth, suspended fine particles, small casting bubbles and believable subsurface transmission", avoid: "opaque plastic, glass sharpness, dirty cloudiness, extra inclusions", strength: 68 }),
  preset({ id: "xray", label: "X 光透视", category: "未来视觉", summary: "半透明结构、内部层级", swatch: "linear-gradient(135deg,#071329,#245b88 55%,#bdefff)", prompt: "stylized x-ray radiograph, translucent outer silhouette, internally coherent structural layers, luminous cyan-white density variation on a deep navy field", avoid: "medical labels, gore, random skeleton, flat inverted photo", strength: 76 }),
  preset({ id: "thermal", label: "热成像", category: "未来视觉", summary: "温度梯度、热边界", swatch: "linear-gradient(135deg,#11146b,#c223aa 38%,#f05b1c 66%,#fff56b)", prompt: "scientific thermal-imaging visualization, coherent temperature zones, smooth false-color heat gradients, clear hot-edge falloff and restrained sensor noise", avoid: "arbitrary rainbow, visible normal colors, text overlay, flat posterization", strength: 72 }),
  preset({ id: "bioluminescent", label: "生物荧光", category: "未来视觉", summary: "内部发光、脉络光路", swatch: "linear-gradient(135deg,#061d2b,#00bfa5 48%,#80f7e8)", prompt: "bioluminescent organic material, internal cyan-green light traveling through fine vascular patterns, dim wet surface, localized glow spill and dark adapted surroundings", avoid: "neon outline sticker, uniform glow, fantasy particles everywhere, lost form", strength: 74 }),
  preset({ id: "voxel", label: "体素世界", category: "未来视觉", summary: "立方体采样、像素体积", swatch: "linear-gradient(135deg,#5c4bc0,#62b9d3 52%,#e3bd63)", prompt: "coherent voxel reconstruction, uniform small cubic volume units, stepped contours, simplified lighting per voxel plane and preserved recognizable proportions", avoid: "flat pixel art, random block sizes, toy bricks, melted cubes", strength: 80 }),
  preset({ id: "low-poly", label: "低多边形", category: "未来视觉", summary: "折面结构、克制渐变", swatch: "linear-gradient(135deg,#244a6c,#6ea2a3 55%,#e1a76f)", prompt: "low-poly faceted reconstruction, purposeful planar topology, crisp silhouette, coherent normal-based shading and restrained polygon density around important features", avoid: "crystal material, random triangulation, smooth CGI, lost identity", strength: 70 }),
  preset({ id: "glitch-scan", label: "扫描故障", category: "未来视觉", summary: "行位移、色散、信号断层", swatch: "linear-gradient(135deg,#0b0c12,#ff2f87 45%,#32e2e2 62%,#16171d)", prompt: "controlled digital scan corruption, localized horizontal displacement, restrained RGB channel separation, data-moshing blocks at selected edges while the main subject remains readable", avoid: "full-frame noise, unreadable subject, random text, excessive chromatic blur", strength: 64 }),
];

export const STYLE_CATEGORIES: Array<"全部" | StyleCategory> = ["全部", "材质幻化", "手作工艺", "玩具微缩", "绘画印刷", "自然实验", "未来视觉"];

type RecipePart = { label: string; prompt: string };
export type StyleRecipe = { medium: RecipePart; material: RecipePart; finish: RecipePart; palette: RecipePart; lighting: RecipePart };

export const STYLE_DIMENSIONS: Record<keyof StyleRecipe, RecipePart[]> = {
  medium: [
    { label: "定格微缩", prompt: "handcrafted stop-motion miniature" }, { label: "产品摄影", prompt: "controlled studio product photograph" },
    { label: "立体插画", prompt: "tactile three-dimensional editorial illustration" }, { label: "博物标本", prompt: "carefully staged museum specimen" },
    { label: "舞台模型", prompt: "theatrical scale-model tableau" }, { label: "实验海报", prompt: "experimental material-study poster" },
  ],
  material: [
    { label: "火山玻璃", prompt: "black volcanic glass with translucent ember fissures" }, { label: "糖果树脂", prompt: "candy-colored translucent cast resin" },
    { label: "针织铜线", prompt: "finely knitted oxidized copper wire" }, { label: "珍珠泡沫", prompt: "pearl-finish microcellular foam" },
    { label: "月光陶瓷", prompt: "pale lunar ceramic with mineral glaze" }, { label: "生物凝胶", prompt: "semi-transparent biopolymer gel" },
    { label: "纸浆岩层", prompt: "compressed paper-pulp geological strata" }, { label: "磁性流体", prompt: "glossy ferrofluid held in controlled ridges" },
  ],
  finish: [
    { label: "手工接缝", prompt: "visible precise handmade seams and minor alignment variation" }, { label: "气泡包裹", prompt: "sparse trapped micro-bubbles and layered internal depth" },
    { label: "矿物结晶", prompt: "small mineral crystals growing along structural edges" }, { label: "旧化包浆", prompt: "subtle hand-worn patina concentrated at contact points" },
    { label: "丝线浮雕", prompt: "directional thread relief following the original volume" }, { label: "湿润表面", prompt: "thin moisture film with restrained specular response" },
  ],
  palette: [
    { label: "熔岩夜色", prompt: "charcoal black, ember orange and muted sulfur yellow" }, { label: "深海生光", prompt: "midnight blue, teal bioluminescence and pearl white" },
    { label: "糖果实验", prompt: "coral pink, aqua, lemon and milky white" }, { label: "氧化金属", prompt: "verdigris, aged copper and smoky graphite" },
    { label: "月面低饱和", prompt: "bone white, lunar gray and a trace of cold violet" }, { label: "植物标本", prompt: "moss green, dried rose, parchment and umber" },
  ],
  lighting: [
    { label: "透射焦散", prompt: "side transmitted light producing believable colored caustics" }, { label: "柔箱塑形", prompt: "large softbox key with controlled rim separation" },
    { label: "内部发光", prompt: "localized internal emission that illuminates nearby material" }, { label: "博物馆顶光", prompt: "quiet overhead museum lighting with soft grounding shadow" },
    { label: "轮廓逆光", prompt: "narrow back rim revealing thickness and fibers" }, { label: "阴天漫射", prompt: "broad overcast diffusion with restrained reflections" },
  ],
};

export function createStyleRecipe(random: () => number = Math.random): StyleRecipe {
  const pick = <T,>(items: T[]) => items[Math.min(items.length - 1, Math.floor(random() * items.length))];
  return {
    medium: pick(STYLE_DIMENSIONS.medium), material: pick(STYLE_DIMENSIONS.material), finish: pick(STYLE_DIMENSIONS.finish),
    palette: pick(STYLE_DIMENSIONS.palette), lighting: pick(STYLE_DIMENSIONS.lighting),
  };
}

const SCOPE_LABELS: Record<string, string> = {
  subject: "只转换主体及其随身物件的材质，背景保持原样",
  whole: "整张画面使用同一套媒介、材质与工艺语言",
  background: "只转换背景环境，主体外观保持原样",
};

const PRESERVE_LABELS: Record<string, string> = {
  identity: "主体身份、数量和可识别特征",
  pose: "姿势、动作与视线方向",
  composition: "画面比例、构图、机位、焦段感和物体位置",
  background: "背景空间、透视和几何结构",
  lighting: "原图的主光方向与明暗关系",
};

export function styleSummary(payload: Record<string, unknown>) {
  const selected = STYLE_PRESETS.find((item) => item.id === payload.style_preset);
  const recipe = payload.style_recipe as StyleRecipe | undefined;
  const custom = String(payload.style_custom || "").trim();
  if (selected) return selected.label;
  if (recipe?.material?.label) return `${recipe.material.label} · ${recipe.medium.label}`;
  if (custom) return custom.length > 8 ? `${custom.slice(0, 8)}…` : custom;
  return "探索风格";
}

export function compileStylePrompt(payload: Record<string, unknown>, hasReference: boolean) {
  const selected = STYLE_PRESETS.find((item) => item.id === payload.style_preset);
  const recipe = payload.style_recipe as StyleRecipe | undefined;
  const custom = String(payload.style_custom || "").trim();
  const styleParts = [selected?.prompt, recipe && Object.values(recipe).map((item) => item.prompt).join(", "), custom].filter(Boolean);
  if (!styleParts.length) return "";
  const preserve = Array.isArray(payload.style_preserve) ? payload.style_preserve.map(String) : ["identity", "pose", "composition", "background", "lighting"];
  const strength = Math.max(20, Math.min(100, Number(payload.style_strength || selected?.strength || 70)));
  const intensity = strength < 50 ? "轻度风格化，原图质感仍占主导" : strength < 78 ? "明显风格化，材质与工艺必须清楚可辨" : "强风格重塑，但所有被锁定结构仍不得漂移";
  const lines = [
    hasReference ? "以输入图片为唯一结构基准，执行图生图风格转换。" : "按以下风格配方生成画面。",
    hasReference && preserve.length ? `保持完全不变：${preserve.map((key) => PRESERVE_LABELS[key]).filter(Boolean).join("；")}。` : "",
    `只改变：${SCOPE_LABELS[String(payload.style_scope || "whole")]}。`,
    `风格配方：${styleParts.join("；")}。`,
    `风格强度：${intensity}。材质必须遵守真实的反射、粗糙度、透明度、厚度、接缝与重力关系，重要轮廓和五官保持可读。`,
    selected?.avoid ? `避免：${selected.avoid}。` : "避免：无关物体、重复主体、结构漂移、廉价滤镜感。",
  ];
  return lines.filter(Boolean).join("\n");
}

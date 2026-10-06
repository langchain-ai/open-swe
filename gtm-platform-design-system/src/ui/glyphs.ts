/*
 * The glyph barrel.
 *
 * Heroicons SOLID is the product's one icon set (Amal, 2026-08-05). Solid has
 * no stroke, which is why the stroke-weight law that used to live in `Icon` is
 * gone: there is no weight left to pick, and so no 1.5-versus-1.75 argument to
 * lose.
 *
 * Heroicons ships three *drawn* sets rather than one drawing scaled three ways:
 * 24 for large use, 20 redrawn with heavier detail for mid sizes, 16 redrawn
 * again with the detail stripped out so a filled glyph does not clot at small
 * sizes. A glyph is therefore not one component here, it is the triple, and
 * `<Icon>` spends the right member for the rung it was asked for. That is the
 * whole reason this barrel exports records instead of components: a call site
 * that held a single component reference could not honour the three sets.
 *
 * Every name below is a job the product already asked for, and the names are
 * the ones the call sites already use. Where Heroicons draws no equivalent the
 * name keeps its job on the nearest solid form, and the choice is recorded in
 * the table below rather than left for a reader to reverse-engineer.
 *
 * Judgment calls, one line each:
 *   Bot            -> CpuChip           Heroicons draws no robot; the machine
 *                                       chip is the closest "not a person".
 *   Circle         -> StopCircle        There is no plain disc in the set; the
 *                                       stop ring is the only bare circle
 *                                       silhouette, and the job is a status dot.
 *   CornerDownLeft -> ArrowTurnDownLeft The return-key arrow, exactly.
 *   FileEdit       -> PencilSquare      No document-with-pencil exists; the
 *                                       framed pencil keeps "edit this thing".
 *   ListChecks     -> ClipboardDocumentCheck  The only checked-list drawing.
 *   PanelLeft      -> Bars3             The sidebar toggle reads as the bars.
 *   PanelRight     -> RectangleGroup    A panelled layout, not a hamburger, so
 *                                       the two panel toggles stay distinct.
 *   Pin / PinFilled / PinOff -> MapPin
 *                                       Bookmark is a save mark. MapPin is
 *                                       the set's pin. There is no unpin
 *                                       drawing; the off state is the same
 *                                       pin, and the label (or primary ink)
 *                                       says which way the control faces.
 *   Loader2 / RefreshCw -> ArrowPath    One drawing, two jobs (spinner, reload).
 *   FileSpreadsheet / Table2 -> TableCells   Likewise: the set draws one grid.
 *
 * Adding a glyph is three lines, one per set, plus its entry. Importing
 * `@heroicons/react` anywhere else is banned by eslint, including elsewhere in
 * `components/ui`: this file is the whole surface.
 *
 * One name collides on purpose: `Box` shares a name with the layout primitive
 * in `components/ui/box`. A file that needs both aliases the glyph at its
 * import (`import { Box as BoxGlyph }`); the barrel does not rename it, because
 * the closed list is worth more than the convenience.
 */

import { LockClosedIcon as LockMicro } from "@heroicons/react/16/solid";
import { LockClosedIcon as LockMini } from "@heroicons/react/20/solid";
import { LockClosedIcon as LockFull } from "@heroicons/react/24/solid";
import type { ComponentType, SVGProps } from "react";

import { OrbFull, OrbMicro, OrbMini } from "./orb-glyph";
import { SpinnerFull, SpinnerMicro, SpinnerMini } from "./spinner-glyph";
import { PushPinFull, PushPinMicro, PushPinMini } from "./pushpin-glyph";

import {
  ArrowDownIcon as ArrowDownMicro,
  ArrowLeftIcon as ArrowLeftMicro,
  ArrowPathIcon as ArrowPathMicro,
  ArrowRightIcon as ArrowRightMicro,
  ArrowTopRightOnSquareIcon as ArrowTopRightOnSquareMicro,
  ArrowTrendingUpIcon as ArrowTrendingUpMicro,
  ArrowTurnDownLeftIcon as ArrowTurnDownLeftMicro,
  ArrowUpIcon as ArrowUpMicro,
  ArrowUpRightIcon as ArrowUpRightMicro,
  ArrowUpTrayIcon as ArrowUpTrayMicro,
  ArrowUturnLeftIcon as ArrowUturnLeftMicro,
  ArrowsPointingOutIcon as ArrowsPointingOutMicro,
  ArchiveBoxIcon as ArchiveBoxMicro,
  Bars3Icon as Bars3Micro,
  BellIcon as BellMicro,
  BoltIcon as BoltMicro,
  BuildingOffice2Icon as BuildingOffice2Micro,
  CalendarIcon as CalendarMicro,
  CalendarDaysIcon as CalendarDaysMicro,
  ChatBubbleLeftIcon as ChatBubbleLeftMicro,
  CheckIcon as CheckMicro,
  CheckCircleIcon as CheckCircleMicro,
  ChevronDoubleLeftIcon as ChevronDoubleLeftMicro,
  ChevronDoubleRightIcon as ChevronDoubleRightMicro,
  ChevronDownIcon as ChevronDownMicro,
  ChevronLeftIcon as ChevronLeftMicro,
  ChevronRightIcon as ChevronRightMicro,
  ChevronUpIcon as ChevronUpMicro,
  ChevronUpDownIcon as ChevronUpDownMicro,
  CircleStackIcon as CircleStackMicro,
  ClipboardDocumentCheckIcon as ClipboardDocumentCheckMicro,
  ClockIcon as ClockMicro,
  CloudIcon as CloudMicro,
  Cog6ToothIcon as Cog6ToothMicro,
  CommandLineIcon as CommandLineMicro,
  CpuChipIcon as CpuChipMicro,
  CubeIcon as CubeMicro,
  CurrencyDollarIcon as CurrencyDollarMicro,
  DocumentIcon as DocumentMicro,
  DocumentDuplicateIcon as DocumentDuplicateMicro,
  DocumentPlusIcon as DocumentPlusMicro,
  DocumentTextIcon as DocumentTextMicro,
  EllipsisHorizontalIcon as EllipsisHorizontalMicro,
  EnvelopeIcon as EnvelopeMicro,
  ExclamationTriangleIcon as ExclamationTriangleMicro,
  FolderIcon as FolderMicro,
  FolderOpenIcon as FolderOpenMicro,
  FunnelIcon as FunnelMicro,
  GlobeAltIcon as GlobeAltMicro,
  HomeIcon as HomeMicro,
  InboxIcon as InboxMicro,
  InformationCircleIcon as InformationCircleMicro,
  ListBulletIcon as ListBulletMicro,
  MagnifyingGlassIcon as MagnifyingGlassMicro,
  MapPinIcon as MapPinMicro,
  MegaphoneIcon as MegaphoneMicro,
  MinusIcon as MinusMicro,
  MoonIcon as MoonMicro,
  PaperAirplaneIcon as PaperAirplaneMicro,
  PaperClipIcon as PaperClipMicro,
  PauseIcon as PauseMicro,
  PencilIcon as PencilMicro,
  PencilSquareIcon as PencilSquareMicro,
  PhotoIcon as PhotoMicro,
  PlayIcon as PlayMicro,
  PlusIcon as PlusMicro,
  PlusCircleIcon as PlusCircleMicro,
  PresentationChartLineIcon as PresentationChartLineMicro,
  QuestionMarkCircleIcon as QuestionMarkCircleMicro,
  RectangleGroupIcon as RectangleGroupMicro,
  ServerStackIcon as ServerMicro,
  ShareIcon as ShareMicro,
  Square3Stack3DIcon as Square3Stack3DMicro,
  Squares2X2Icon as Squares2X2Micro,
  StarIcon as StarMicro,
  StopIcon as StopMicro,
  StopCircleIcon as StopCircleMicro,
  SunIcon as SunMicro,
  SwatchIcon as SwatchMicro,
  TableCellsIcon as TableCellsMicro,
  TrashIcon as TrashMicro,
  UserIcon as UserMicro,
  UsersIcon as UsersMicro,
  ViewColumnsIcon as ViewColumnsMicro,
  ViewfinderCircleIcon as ViewfinderCircleMicro,
  WrenchIcon as WrenchMicro,
  XCircleIcon as XCircleMicro,
  XMarkIcon as XMarkMicro,
} from "@heroicons/react/16/solid";

import {
  ArrowDownIcon as ArrowDownMini,
  ArrowLeftIcon as ArrowLeftMini,
  ArrowPathIcon as ArrowPathMini,
  ArrowRightIcon as ArrowRightMini,
  ArrowTopRightOnSquareIcon as ArrowTopRightOnSquareMini,
  ArrowTrendingUpIcon as ArrowTrendingUpMini,
  ArrowTurnDownLeftIcon as ArrowTurnDownLeftMini,
  ArrowUpIcon as ArrowUpMini,
  ArrowUpRightIcon as ArrowUpRightMini,
  ArrowUpTrayIcon as ArrowUpTrayMini,
  ArrowUturnLeftIcon as ArrowUturnLeftMini,
  ArrowsPointingOutIcon as ArrowsPointingOutMini,
  ArchiveBoxIcon as ArchiveBoxMini,
  Bars3Icon as Bars3Mini,
  BellIcon as BellMini,
  BoltIcon as BoltMini,
  BuildingOffice2Icon as BuildingOffice2Mini,
  CalendarIcon as CalendarMini,
  CalendarDaysIcon as CalendarDaysMini,
  ChatBubbleLeftIcon as ChatBubbleLeftMini,
  CheckIcon as CheckMini,
  CheckCircleIcon as CheckCircleMini,
  ChevronDoubleLeftIcon as ChevronDoubleLeftMini,
  ChevronDoubleRightIcon as ChevronDoubleRightMini,
  ChevronDownIcon as ChevronDownMini,
  ChevronLeftIcon as ChevronLeftMini,
  ChevronRightIcon as ChevronRightMini,
  ChevronUpIcon as ChevronUpMini,
  ChevronUpDownIcon as ChevronUpDownMini,
  CircleStackIcon as CircleStackMini,
  ClipboardDocumentCheckIcon as ClipboardDocumentCheckMini,
  ClockIcon as ClockMini,
  CloudIcon as CloudMini,
  Cog6ToothIcon as Cog6ToothMini,
  CommandLineIcon as CommandLineMini,
  CpuChipIcon as CpuChipMini,
  CubeIcon as CubeMini,
  CurrencyDollarIcon as CurrencyDollarMini,
  DocumentIcon as DocumentMini,
  DocumentDuplicateIcon as DocumentDuplicateMini,
  DocumentPlusIcon as DocumentPlusMini,
  DocumentTextIcon as DocumentTextMini,
  EllipsisHorizontalIcon as EllipsisHorizontalMini,
  EnvelopeIcon as EnvelopeMini,
  ExclamationTriangleIcon as ExclamationTriangleMini,
  FolderIcon as FolderMini,
  FolderOpenIcon as FolderOpenMini,
  FunnelIcon as FunnelMini,
  GlobeAltIcon as GlobeAltMini,
  HomeIcon as HomeMini,
  InboxIcon as InboxMini,
  InformationCircleIcon as InformationCircleMini,
  ListBulletIcon as ListBulletMini,
  MagnifyingGlassIcon as MagnifyingGlassMini,
  MapPinIcon as MapPinMini,
  MegaphoneIcon as MegaphoneMini,
  MinusIcon as MinusMini,
  MoonIcon as MoonMini,
  PaperAirplaneIcon as PaperAirplaneMini,
  PaperClipIcon as PaperClipMini,
  PauseIcon as PauseMini,
  PencilIcon as PencilMini,
  PencilSquareIcon as PencilSquareMini,
  PhotoIcon as PhotoMini,
  PlayIcon as PlayMini,
  PlusIcon as PlusMini,
  PlusCircleIcon as PlusCircleMini,
  PresentationChartLineIcon as PresentationChartLineMini,
  QuestionMarkCircleIcon as QuestionMarkCircleMini,
  RectangleGroupIcon as RectangleGroupMini,
  ServerStackIcon as ServerMini,
  ShareIcon as ShareMini,
  Square3Stack3DIcon as Square3Stack3DMini,
  Squares2X2Icon as Squares2X2Mini,
  StarIcon as StarMini,
  StopIcon as StopMini,
  StopCircleIcon as StopCircleMini,
  SunIcon as SunMini,
  SwatchIcon as SwatchMini,
  TableCellsIcon as TableCellsMini,
  TrashIcon as TrashMini,
  UserIcon as UserMini,
  UsersIcon as UsersMini,
  ViewColumnsIcon as ViewColumnsMini,
  ViewfinderCircleIcon as ViewfinderCircleMini,
  WrenchIcon as WrenchMini,
  XCircleIcon as XCircleMini,
  XMarkIcon as XMarkMini,
} from "@heroicons/react/20/solid";

import {
  ArrowDownIcon as ArrowDownFull,
  ArrowLeftIcon as ArrowLeftFull,
  ArrowPathIcon as ArrowPathFull,
  ArrowRightIcon as ArrowRightFull,
  ArrowTopRightOnSquareIcon as ArrowTopRightOnSquareFull,
  ArrowTrendingUpIcon as ArrowTrendingUpFull,
  ArrowTurnDownLeftIcon as ArrowTurnDownLeftFull,
  ArrowUpIcon as ArrowUpFull,
  ArrowUpRightIcon as ArrowUpRightFull,
  ArrowUpTrayIcon as ArrowUpTrayFull,
  ArrowUturnLeftIcon as ArrowUturnLeftFull,
  ArrowsPointingOutIcon as ArrowsPointingOutFull,
  ArchiveBoxIcon as ArchiveBoxFull,
  Bars3Icon as Bars3Full,
  BellIcon as BellFull,
  BoltIcon as BoltFull,
  BuildingOffice2Icon as BuildingOffice2Full,
  CalendarIcon as CalendarFull,
  CalendarDaysIcon as CalendarDaysFull,
  ChatBubbleLeftIcon as ChatBubbleLeftFull,
  CheckIcon as CheckFull,
  CheckCircleIcon as CheckCircleFull,
  ChevronDoubleLeftIcon as ChevronDoubleLeftFull,
  ChevronDoubleRightIcon as ChevronDoubleRightFull,
  ChevronDownIcon as ChevronDownFull,
  ChevronLeftIcon as ChevronLeftFull,
  ChevronRightIcon as ChevronRightFull,
  ChevronUpIcon as ChevronUpFull,
  ChevronUpDownIcon as ChevronUpDownFull,
  CircleStackIcon as CircleStackFull,
  ClipboardDocumentCheckIcon as ClipboardDocumentCheckFull,
  ClockIcon as ClockFull,
  CloudIcon as CloudFull,
  Cog6ToothIcon as Cog6ToothFull,
  CommandLineIcon as CommandLineFull,
  CpuChipIcon as CpuChipFull,
  CubeIcon as CubeFull,
  CurrencyDollarIcon as CurrencyDollarFull,
  DocumentIcon as DocumentFull,
  DocumentDuplicateIcon as DocumentDuplicateFull,
  DocumentPlusIcon as DocumentPlusFull,
  DocumentTextIcon as DocumentTextFull,
  EllipsisHorizontalIcon as EllipsisHorizontalFull,
  EnvelopeIcon as EnvelopeFull,
  ExclamationTriangleIcon as ExclamationTriangleFull,
  FolderIcon as FolderFull,
  FolderOpenIcon as FolderOpenFull,
  FunnelIcon as FunnelFull,
  GlobeAltIcon as GlobeAltFull,
  HomeIcon as HomeFull,
  InboxIcon as InboxFull,
  InformationCircleIcon as InformationCircleFull,
  ListBulletIcon as ListBulletFull,
  MagnifyingGlassIcon as MagnifyingGlassFull,
  MapPinIcon as MapPinFull,
  MegaphoneIcon as MegaphoneFull,
  MinusIcon as MinusFull,
  MoonIcon as MoonFull,
  PaperAirplaneIcon as PaperAirplaneFull,
  PaperClipIcon as PaperClipFull,
  PauseIcon as PauseFull,
  PencilIcon as PencilFull,
  PencilSquareIcon as PencilSquareFull,
  PhotoIcon as PhotoFull,
  PlayIcon as PlayFull,
  PlusIcon as PlusFull,
  PlusCircleIcon as PlusCircleFull,
  PresentationChartLineIcon as PresentationChartLineFull,
  QuestionMarkCircleIcon as QuestionMarkCircleFull,
  RectangleGroupIcon as RectangleGroupFull,
  ServerStackIcon as ServerFull,
  ShareIcon as ShareFull,
  Square3Stack3DIcon as Square3Stack3DFull,
  Squares2X2Icon as Squares2X2Full,
  StarIcon as StarFull,
  StopIcon as StopFull,
  StopCircleIcon as StopCircleFull,
  SunIcon as SunFull,
  SwatchIcon as SwatchFull,
  TableCellsIcon as TableCellsFull,
  TrashIcon as TrashFull,
  UserIcon as UserFull,
  UsersIcon as UsersFull,
  ViewColumnsIcon as ViewColumnsFull,
  ViewfinderCircleIcon as ViewfinderCircleFull,
  WrenchIcon as WrenchFull,
  XCircleIcon as XCircleFull,
  XMarkIcon as XMarkFull,
} from "@heroicons/react/24/solid";

/** One drawn Heroicon. Solid only; the set carries no stroke to configure. */
type GlyphComponent = ComponentType<
  SVGProps<SVGSVGElement> & { title?: string; titleId?: string }
>;

/**
 * A glyph is the three drawn sets, not one drawing. `<Icon>` picks the member
 * that matches the rung; nothing else reads these fields.
 */
interface Glyph {
  /** 16/solid, the micro set. Also what the 14px rung scales down. */
  readonly micro: GlyphComponent;
  /** 20/solid, the mini set. */
  readonly mini: GlyphComponent;
  /** 24/solid, the full set. */
  readonly full: GlyphComponent;
}

function glyph(micro: GlyphComponent, mini: GlyphComponent, full: GlyphComponent): Glyph {
  return { full, micro, mini };
}

const Lock: Glyph = glyph(LockMicro, LockMini, LockFull);
const AlertTriangle: Glyph = glyph(ExclamationTriangleMicro, ExclamationTriangleMini, ExclamationTriangleFull);
const ArrowDown: Glyph = glyph(ArrowDownMicro, ArrowDownMini, ArrowDownFull);
const ArrowLeft: Glyph = glyph(ArrowLeftMicro, ArrowLeftMini, ArrowLeftFull);
const ArrowRight: Glyph = glyph(ArrowRightMicro, ArrowRightMini, ArrowRightFull);
const ArrowUp: Glyph = glyph(ArrowUpMicro, ArrowUpMini, ArrowUpFull);
const ArrowUpRight: Glyph = glyph(ArrowUpRightMicro, ArrowUpRightMini, ArrowUpRightFull);
const ArchiveBox: Glyph = glyph(ArchiveBoxMicro, ArchiveBoxMini, ArchiveBoxFull);
const Bell: Glyph = glyph(BellMicro, BellMini, BellFull);
const Bot: Glyph = glyph(CpuChipMicro, CpuChipMini, CpuChipFull);
const Box: Glyph = glyph(CubeMicro, CubeMini, CubeFull);
const Building2: Glyph = glyph(BuildingOffice2Micro, BuildingOffice2Mini, BuildingOffice2Full);
const Calendar: Glyph = glyph(CalendarMicro, CalendarMini, CalendarFull);
const CalendarDays: Glyph = glyph(CalendarDaysMicro, CalendarDaysMini, CalendarDaysFull);
const Check: Glyph = glyph(CheckMicro, CheckMini, CheckFull);
const CheckCircle: Glyph = glyph(CheckCircleMicro, CheckCircleMini, CheckCircleFull);
const ChevronDown: Glyph = glyph(ChevronDownMicro, ChevronDownMini, ChevronDownFull);
const ChevronLeft: Glyph = glyph(ChevronLeftMicro, ChevronLeftMini, ChevronLeftFull);
const ChevronRight: Glyph = glyph(ChevronRightMicro, ChevronRightMini, ChevronRightFull);
const ChevronUp: Glyph = glyph(ChevronUpMicro, ChevronUpMini, ChevronUpFull);
const ChevronsLeft: Glyph = glyph(ChevronDoubleLeftMicro, ChevronDoubleLeftMini, ChevronDoubleLeftFull);
const ChevronsRight: Glyph = glyph(ChevronDoubleRightMicro, ChevronDoubleRightMini, ChevronDoubleRightFull);
const ChevronsUpDown: Glyph = glyph(ChevronUpDownMicro, ChevronUpDownMini, ChevronUpDownFull);
const Circle: Glyph = glyph(StopCircleMicro, StopCircleMini, StopCircleFull);
const Clock: Glyph = glyph(ClockMicro, ClockMini, ClockFull);
const Cloud: Glyph = glyph(CloudMicro, CloudMini, CloudFull);
const Columns: Glyph = glyph(ViewColumnsMicro, ViewColumnsMini, ViewColumnsFull);
const Copy: Glyph = glyph(DocumentDuplicateMicro, DocumentDuplicateMini, DocumentDuplicateFull);
const CornerDownLeft: Glyph = glyph(ArrowTurnDownLeftMicro, ArrowTurnDownLeftMini, ArrowTurnDownLeftFull);
const Database: Glyph = glyph(CircleStackMicro, CircleStackMini, CircleStackFull);
const DollarSign: Glyph = glyph(CurrencyDollarMicro, CurrencyDollarMini, CurrencyDollarFull);
const Ellipsis: Glyph = glyph(EllipsisHorizontalMicro, EllipsisHorizontalMini, EllipsisHorizontalFull);
const Expand: Glyph = glyph(ArrowsPointingOutMicro, ArrowsPointingOutMini, ArrowsPointingOutFull);
const ExternalLink: Glyph = glyph(ArrowTopRightOnSquareMicro, ArrowTopRightOnSquareMini, ArrowTopRightOnSquareFull);
const File: Glyph = glyph(DocumentMicro, DocumentMini, DocumentFull);
const FileEdit: Glyph = glyph(PencilSquareMicro, PencilSquareMini, PencilSquareFull);
const FilePlus: Glyph = glyph(DocumentPlusMicro, DocumentPlusMini, DocumentPlusFull);
const FileSpreadsheet: Glyph = glyph(TableCellsMicro, TableCellsMini, TableCellsFull);
const FileText: Glyph = glyph(DocumentTextMicro, DocumentTextMini, DocumentTextFull);
const Filter: Glyph = glyph(FunnelMicro, FunnelMini, FunnelFull);
const Folder: Glyph = glyph(FolderMicro, FolderMini, FolderFull);
const FolderOpen: Glyph = glyph(FolderOpenMicro, FolderOpenMini, FolderOpenFull);
const Globe: Glyph = glyph(GlobeAltMicro, GlobeAltMini, GlobeAltFull);
const Home: Glyph = glyph(HomeMicro, HomeMini, HomeFull);
const Image: Glyph = glyph(PhotoMicro, PhotoMini, PhotoFull);
const Inbox: Glyph = glyph(InboxMicro, InboxMini, InboxFull);
const Info: Glyph = glyph(InformationCircleMicro, InformationCircleMini, InformationCircleFull);
const Layers: Glyph = glyph(Square3Stack3DMicro, Square3Stack3DMini, Square3Stack3DFull);
const LayoutGrid: Glyph = glyph(Squares2X2Micro, Squares2X2Mini, Squares2X2Full);
const LineChart: Glyph = glyph(PresentationChartLineMicro, PresentationChartLineMini, PresentationChartLineFull);
const List: Glyph = glyph(ListBulletMicro, ListBulletMini, ListBulletFull);
const ListChecks: Glyph = glyph(ClipboardDocumentCheckMicro, ClipboardDocumentCheckMini, ClipboardDocumentCheckFull);
/*
 * The one loading glyph. Heroicons solid has no indeterminate ring -- its
 * ArrowPath is a solid refresh arrow, which reads as "retry", not "working" --
 * so this rung is drawn in `spinner-glyph.tsx` and enters through the barrel
 * like every other glyph.
 */
const Loader2: Glyph = glyph(SpinnerMicro, SpinnerMini, SpinnerFull);
const Mail: Glyph = glyph(EnvelopeMicro, EnvelopeMini, EnvelopeFull);
const Megaphone: Glyph = glyph(MegaphoneMicro, MegaphoneMini, MegaphoneFull);
const MessageSquare: Glyph = glyph(ChatBubbleLeftMicro, ChatBubbleLeftMini, ChatBubbleLeftFull);
const Minus: Glyph = glyph(MinusMicro, MinusMini, MinusFull);
const Moon: Glyph = glyph(MoonMicro, MoonMini, MoonFull);
/* The still frame of the thinking orb, for surfaces that name it as an icon. */
const Orb: Glyph = glyph(OrbMicro, OrbMini, OrbFull);

const Palette: Glyph = glyph(SwatchMicro, SwatchMini, SwatchFull);
const PanelLeft: Glyph = glyph(Bars3Micro, Bars3Mini, Bars3Full);
const PanelRight: Glyph = glyph(RectangleGroupMicro, RectangleGroupMini, RectangleGroupFull);
const Paperclip: Glyph = glyph(PaperClipMicro, PaperClipMini, PaperClipFull);
const Pause: Glyph = glyph(PauseMicro, PauseMini, PauseFull);
const Pencil: Glyph = glyph(PencilMicro, PencilMini, PencilFull);
const Pin: Glyph = glyph(MapPinMicro, MapPinMini, MapPinFull);
const PinFilled: Glyph = glyph(MapPinMicro, MapPinMini, MapPinFull);
const PinOff: Glyph = glyph(MapPinMicro, MapPinMini, MapPinFull);
/* Pin actions use the thumbtack; legacy Pin also serves geographic call sites. */
const PushPin: Glyph = glyph(PushPinMicro, PushPinMini, PushPinFull);
const Play: Glyph = glyph(PlayMicro, PlayMini, PlayFull);
const Plus: Glyph = glyph(PlusMicro, PlusMini, PlusFull);
const PlusCircle: Glyph = glyph(PlusCircleMicro, PlusCircleMini, PlusCircleFull);
const QuestionMarkCircle: Glyph = glyph(QuestionMarkCircleMicro, QuestionMarkCircleMini, QuestionMarkCircleFull);
const RefreshCw: Glyph = glyph(ArrowPathMicro, ArrowPathMini, ArrowPathFull);
const RotateCcw: Glyph = glyph(ArrowUturnLeftMicro, ArrowUturnLeftMini, ArrowUturnLeftFull);
const Search: Glyph = glyph(MagnifyingGlassMicro, MagnifyingGlassMini, MagnifyingGlassFull);
const Send: Glyph = glyph(PaperAirplaneMicro, PaperAirplaneMini, PaperAirplaneFull);
const Server: Glyph = glyph(ServerMicro, ServerMini, ServerFull);
const Settings2: Glyph = glyph(Cog6ToothMicro, Cog6ToothMini, Cog6ToothFull);
const Share2: Glyph = glyph(ShareMicro, ShareMini, ShareFull);
const Square: Glyph = glyph(StopMicro, StopMini, StopFull);
const Star: Glyph = glyph(StarMicro, StarMini, StarFull);
const Sun: Glyph = glyph(SunMicro, SunMini, SunFull);
const Table2: Glyph = glyph(TableCellsMicro, TableCellsMini, TableCellsFull);
const Target: Glyph = glyph(ViewfinderCircleMicro, ViewfinderCircleMini, ViewfinderCircleFull);
const Terminal: Glyph = glyph(CommandLineMicro, CommandLineMini, CommandLineFull);
const Trash2: Glyph = glyph(TrashMicro, TrashMini, TrashFull);
const TrendingUp: Glyph = glyph(ArrowTrendingUpMicro, ArrowTrendingUpMini, ArrowTrendingUpFull);
const Upload: Glyph = glyph(ArrowUpTrayMicro, ArrowUpTrayMini, ArrowUpTrayFull);
const User: Glyph = glyph(UserMicro, UserMini, UserFull);
const Users: Glyph = glyph(UsersMicro, UsersMini, UsersFull);
const Wrench: Glyph = glyph(WrenchMicro, WrenchMini, WrenchFull);
const X: Glyph = glyph(XMarkMicro, XMarkMini, XMarkFull);
const XCircle: Glyph = glyph(XCircleMicro, XCircleMini, XCircleFull);
const Zap: Glyph = glyph(BoltMicro, BoltMini, BoltFull);

export {
  Lock,
  AlertTriangle,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  ArrowUpRight,
  ArchiveBox,
  Bell,
  Bot,
  Box,
  Building2,
  Calendar,
  CalendarDays,
  Check,
  CheckCircle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  ChevronsLeft,
  ChevronsRight,
  ChevronsUpDown,
  Circle,
  Clock,
  Cloud,
  Columns,
  Copy,
  CornerDownLeft,
  Database,
  DollarSign,
  Ellipsis,
  Expand,
  ExternalLink,
  File,
  FileEdit,
  FilePlus,
  FileSpreadsheet,
  FileText,
  Filter,
  Folder,
  FolderOpen,
  Globe,
  Home,
  Image,
  Inbox,
  Info,
  Layers,
  LayoutGrid,
  LineChart,
  List,
  ListChecks,
  Loader2,
  Mail,
  Megaphone,
  MessageSquare,
  Minus,
  Moon,
  Orb,
  Palette,
  PanelLeft,
  PanelRight,
  Paperclip,
  Pause,
  Pencil,
  Pin,
  PinFilled,
  PinOff,
  PushPin,
  Play,
  Plus,
  PlusCircle,
  QuestionMarkCircle,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  Server,
  Settings2,
  Share2,
  Square,
  Star,
  Sun,
  Table2,
  Target,
  Terminal,
  Trash2,
  TrendingUp,
  Upload,
  User,
  Users,
  Wrench,
  X,
  XCircle,
  Zap,
};
export type { Glyph, GlyphComponent };

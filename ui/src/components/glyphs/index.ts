/*
 * The app's glyph barrel: the design system's closed list, plus the jobs Open
 * SWE asks for that the GTM product never did (git, review, terminal, admin).
 *
 * Same law as the system's barrel: Heroicons solid, one triple per glyph so
 * `<Icon>` spends the drawn set that matches the rung. This file is the only
 * place in the app that imports `@heroicons/react`; call sites import glyphs
 * from here and render them through `Icon`.
 */

import {
  EyeIcon as EyeMicro,
  EyeSlashIcon as EyeOffMicro,
  ArrowDownTrayIcon as DownloadMicro,
  LinkIcon as LinkMicro,
  HashtagIcon as HashMicro,
  SparklesIcon as SparklesMicro,
  BugAntIcon as BugMicro,
  CodeBracketIcon as CodeMicro,
  CodeBracketSquareIcon as CodeSquareMicro,
  FlagIcon as FlagMicro,
  BeakerIcon as BeakerMicro,
  PuzzlePieceIcon as PuzzleMicro,
  CubeIcon as CubeMicro,
  ChartBarIcon as BarChartMicro,
  SignalIcon as ActivityMicro,
  ShieldCheckIcon as ShieldCheckMicro,
  AdjustmentsHorizontalIcon as SlidersMicro,
  ArrowRightStartOnRectangleIcon as LogOutMicro,
  ComputerDesktopIcon as MonitorMicro,
  PaintBrushIcon as BrushMicro,
  BookOpenIcon as BookOpenMicro,
  DocumentMagnifyingGlassIcon as FileSearchMicro,
  HandThumbUpIcon as ThumbsUpMicro,
  HandThumbDownIcon as ThumbsDownMicro,
  LightBulbIcon as LightbulbMicro,
  WrenchScrewdriverIcon as HammerMicro,
  ArrowsUpDownIcon as ArrowUpDownMicro,
  BarsArrowDownIcon as SortDescMicro,
  BarsArrowUpIcon as SortAscMicro,
  ArrowsPointingInIcon as MinimizeMicro,
  Bars3BottomLeftIcon as WrapTextMicro,
  NumberedListIcon as ListOrderedMicro,
  BoldIcon as BoldMicro,
  ItalicIcon as ItalicMicro,
  H1Icon as HeadingMicro,
  NoSymbolIcon as BanMicro,
  ChatBubbleOvalLeftIcon as MessageCircleMicro,
  ChatBubbleLeftRightIcon as MessagesMicro,
  ExclamationCircleIcon as AlertCircleMicro,
  FolderPlusIcon as FolderPlusMicro,
  QueueListIcon as RowsMicro,
  CheckBadgeIcon as CheckBadgeMicro,
  KeyIcon as KeyMicro,
  Square2StackIcon as DuplicateMicro,
  EllipsisVerticalIcon as MoreVerticalMicro,
  RocketLaunchIcon as RocketMicro,
  ArrowPathRoundedSquareIcon as RepeatMicro,
  AtSymbolIcon as AtSignMicro,
  LockOpenIcon as UnlockMicro,
  ArrowUturnRightIcon as RedoMicro,
  ClipboardDocumentListIcon as ClipboardListMicro,
  RectangleStackIcon as StackMicro,
  ArrowsRightLeftIcon as SwitchHorizontalMicro,
  UserCircleIcon as UserCircleMicro,
  FingerPrintIcon as FingerprintMicro,
  MinusCircleIcon as MinusCircleMicro,
} from "@heroicons/react/16/solid"
import {
  EyeIcon as EyeMini,
  EyeSlashIcon as EyeOffMini,
  ArrowDownTrayIcon as DownloadMini,
  LinkIcon as LinkMini,
  HashtagIcon as HashMini,
  SparklesIcon as SparklesMini,
  BugAntIcon as BugMini,
  CodeBracketIcon as CodeMini,
  CodeBracketSquareIcon as CodeSquareMini,
  FlagIcon as FlagMini,
  BeakerIcon as BeakerMini,
  PuzzlePieceIcon as PuzzleMini,
  CubeIcon as CubeMini,
  ChartBarIcon as BarChartMini,
  SignalIcon as ActivityMini,
  ShieldCheckIcon as ShieldCheckMini,
  AdjustmentsHorizontalIcon as SlidersMini,
  ArrowRightStartOnRectangleIcon as LogOutMini,
  ComputerDesktopIcon as MonitorMini,
  PaintBrushIcon as BrushMini,
  BookOpenIcon as BookOpenMini,
  DocumentMagnifyingGlassIcon as FileSearchMini,
  HandThumbUpIcon as ThumbsUpMini,
  HandThumbDownIcon as ThumbsDownMini,
  LightBulbIcon as LightbulbMini,
  WrenchScrewdriverIcon as HammerMini,
  ArrowsUpDownIcon as ArrowUpDownMini,
  BarsArrowDownIcon as SortDescMini,
  BarsArrowUpIcon as SortAscMini,
  ArrowsPointingInIcon as MinimizeMini,
  Bars3BottomLeftIcon as WrapTextMini,
  NumberedListIcon as ListOrderedMini,
  BoldIcon as BoldMini,
  ItalicIcon as ItalicMini,
  H1Icon as HeadingMini,
  NoSymbolIcon as BanMini,
  ChatBubbleOvalLeftIcon as MessageCircleMini,
  ChatBubbleLeftRightIcon as MessagesMini,
  ExclamationCircleIcon as AlertCircleMini,
  FolderPlusIcon as FolderPlusMini,
  QueueListIcon as RowsMini,
  CheckBadgeIcon as CheckBadgeMini,
  KeyIcon as KeyMini,
  Square2StackIcon as DuplicateMini,
  EllipsisVerticalIcon as MoreVerticalMini,
  RocketLaunchIcon as RocketMini,
  ArrowPathRoundedSquareIcon as RepeatMini,
  AtSymbolIcon as AtSignMini,
  LockOpenIcon as UnlockMini,
  ArrowUturnRightIcon as RedoMini,
  ClipboardDocumentListIcon as ClipboardListMini,
  RectangleStackIcon as StackMini,
  ArrowsRightLeftIcon as SwitchHorizontalMini,
  UserCircleIcon as UserCircleMini,
  FingerPrintIcon as FingerprintMini,
  MinusCircleIcon as MinusCircleMini,
} from "@heroicons/react/20/solid"
import {
  EyeIcon as EyeFull,
  EyeSlashIcon as EyeOffFull,
  ArrowDownTrayIcon as DownloadFull,
  LinkIcon as LinkFull,
  HashtagIcon as HashFull,
  SparklesIcon as SparklesFull,
  BugAntIcon as BugFull,
  CodeBracketIcon as CodeFull,
  CodeBracketSquareIcon as CodeSquareFull,
  FlagIcon as FlagFull,
  BeakerIcon as BeakerFull,
  PuzzlePieceIcon as PuzzleFull,
  CubeIcon as CubeFull,
  ChartBarIcon as BarChartFull,
  SignalIcon as ActivityFull,
  ShieldCheckIcon as ShieldCheckFull,
  AdjustmentsHorizontalIcon as SlidersFull,
  ArrowRightStartOnRectangleIcon as LogOutFull,
  ComputerDesktopIcon as MonitorFull,
  PaintBrushIcon as BrushFull,
  BookOpenIcon as BookOpenFull,
  DocumentMagnifyingGlassIcon as FileSearchFull,
  HandThumbUpIcon as ThumbsUpFull,
  HandThumbDownIcon as ThumbsDownFull,
  LightBulbIcon as LightbulbFull,
  WrenchScrewdriverIcon as HammerFull,
  ArrowsUpDownIcon as ArrowUpDownFull,
  BarsArrowDownIcon as SortDescFull,
  BarsArrowUpIcon as SortAscFull,
  ArrowsPointingInIcon as MinimizeFull,
  Bars3BottomLeftIcon as WrapTextFull,
  NumberedListIcon as ListOrderedFull,
  BoldIcon as BoldFull,
  ItalicIcon as ItalicFull,
  H1Icon as HeadingFull,
  NoSymbolIcon as BanFull,
  ChatBubbleOvalLeftIcon as MessageCircleFull,
  ChatBubbleLeftRightIcon as MessagesFull,
  ExclamationCircleIcon as AlertCircleFull,
  FolderPlusIcon as FolderPlusFull,
  QueueListIcon as RowsFull,
  CheckBadgeIcon as CheckBadgeFull,
  KeyIcon as KeyFull,
  Square2StackIcon as DuplicateFull,
  EllipsisVerticalIcon as MoreVerticalFull,
  RocketLaunchIcon as RocketFull,
  ArrowPathRoundedSquareIcon as RepeatFull,
  AtSymbolIcon as AtSignFull,
  LockOpenIcon as UnlockFull,
  ArrowUturnRightIcon as RedoFull,
  ClipboardDocumentListIcon as ClipboardListFull,
  RectangleStackIcon as StackFull,
  ArrowsRightLeftIcon as SwitchHorizontalFull,
  UserCircleIcon as UserCircleFull,
  FingerPrintIcon as FingerprintFull,
  MinusCircleIcon as MinusCircleFull,
} from "@heroicons/react/24/solid"
import type { Glyph } from "@langchain/gtm-platform-design-system/ui/glyphs"

import { DRAWN_GLYPHS } from "./drawn"

export * from "@langchain/gtm-platform-design-system/ui/glyphs"
export type { Glyph } from "@langchain/gtm-platform-design-system/ui/glyphs"

function glyph(micro: Glyph["micro"], mini: Glyph["mini"], full: Glyph["full"]): Glyph {
  return { micro, mini, full }
}

export const Eye: Glyph = glyph(EyeMicro, EyeMini, EyeFull)
export const EyeOff: Glyph = glyph(EyeOffMicro, EyeOffMini, EyeOffFull)
export const Download: Glyph = glyph(DownloadMicro, DownloadMini, DownloadFull)
export const Link: Glyph = glyph(LinkMicro, LinkMini, LinkFull)
export const Hash: Glyph = glyph(HashMicro, HashMini, HashFull)
export const Sparkles: Glyph = glyph(SparklesMicro, SparklesMini, SparklesFull)
export const Bug: Glyph = glyph(BugMicro, BugMini, BugFull)
export const Code: Glyph = glyph(CodeMicro, CodeMini, CodeFull)
export const CodeSquare: Glyph = glyph(CodeSquareMicro, CodeSquareMini, CodeSquareFull)
export const Flag: Glyph = glyph(FlagMicro, FlagMini, FlagFull)
export const Beaker: Glyph = glyph(BeakerMicro, BeakerMini, BeakerFull)
export const Puzzle: Glyph = glyph(PuzzleMicro, PuzzleMini, PuzzleFull)
export const Cube: Glyph = glyph(CubeMicro, CubeMini, CubeFull)
export const BarChart: Glyph = glyph(BarChartMicro, BarChartMini, BarChartFull)
export const Activity: Glyph = glyph(ActivityMicro, ActivityMini, ActivityFull)
export const ShieldCheck: Glyph = glyph(ShieldCheckMicro, ShieldCheckMini, ShieldCheckFull)
export const Sliders: Glyph = glyph(SlidersMicro, SlidersMini, SlidersFull)
export const LogOut: Glyph = glyph(LogOutMicro, LogOutMini, LogOutFull)
export const Monitor: Glyph = glyph(MonitorMicro, MonitorMini, MonitorFull)
export const Brush: Glyph = glyph(BrushMicro, BrushMini, BrushFull)
export const BookOpen: Glyph = glyph(BookOpenMicro, BookOpenMini, BookOpenFull)
export const FileSearch: Glyph = glyph(FileSearchMicro, FileSearchMini, FileSearchFull)
export const ThumbsUp: Glyph = glyph(ThumbsUpMicro, ThumbsUpMini, ThumbsUpFull)
export const ThumbsDown: Glyph = glyph(ThumbsDownMicro, ThumbsDownMini, ThumbsDownFull)
export const Lightbulb: Glyph = glyph(LightbulbMicro, LightbulbMini, LightbulbFull)
export const Hammer: Glyph = glyph(HammerMicro, HammerMini, HammerFull)
export const ArrowUpDown: Glyph = glyph(ArrowUpDownMicro, ArrowUpDownMini, ArrowUpDownFull)
export const SortDesc: Glyph = glyph(SortDescMicro, SortDescMini, SortDescFull)
export const SortAsc: Glyph = glyph(SortAscMicro, SortAscMini, SortAscFull)
export const Minimize: Glyph = glyph(MinimizeMicro, MinimizeMini, MinimizeFull)
export const WrapText: Glyph = glyph(WrapTextMicro, WrapTextMini, WrapTextFull)
export const ListOrdered: Glyph = glyph(ListOrderedMicro, ListOrderedMini, ListOrderedFull)
export const Bold: Glyph = glyph(BoldMicro, BoldMini, BoldFull)
export const Italic: Glyph = glyph(ItalicMicro, ItalicMini, ItalicFull)
export const Heading: Glyph = glyph(HeadingMicro, HeadingMini, HeadingFull)
export const Ban: Glyph = glyph(BanMicro, BanMini, BanFull)
export const MessageCircle: Glyph = glyph(MessageCircleMicro, MessageCircleMini, MessageCircleFull)
export const Messages: Glyph = glyph(MessagesMicro, MessagesMini, MessagesFull)
export const AlertCircle: Glyph = glyph(AlertCircleMicro, AlertCircleMini, AlertCircleFull)
export const FolderPlus: Glyph = glyph(FolderPlusMicro, FolderPlusMini, FolderPlusFull)
export const Rows: Glyph = glyph(RowsMicro, RowsMini, RowsFull)
export const CheckBadge: Glyph = glyph(CheckBadgeMicro, CheckBadgeMini, CheckBadgeFull)
export const Key: Glyph = glyph(KeyMicro, KeyMini, KeyFull)
export const Duplicate: Glyph = glyph(DuplicateMicro, DuplicateMini, DuplicateFull)
export const MoreVertical: Glyph = glyph(MoreVerticalMicro, MoreVerticalMini, MoreVerticalFull)
export const Rocket: Glyph = glyph(RocketMicro, RocketMini, RocketFull)
export const Repeat: Glyph = glyph(RepeatMicro, RepeatMini, RepeatFull)
export const AtSign: Glyph = glyph(AtSignMicro, AtSignMini, AtSignFull)
export const Unlock: Glyph = glyph(UnlockMicro, UnlockMini, UnlockFull)
export const Redo: Glyph = glyph(RedoMicro, RedoMini, RedoFull)
export const ClipboardList: Glyph = glyph(ClipboardListMicro, ClipboardListMini, ClipboardListFull)
export const Stack: Glyph = glyph(StackMicro, StackMini, StackFull)
export const SwitchHorizontal: Glyph = glyph(SwitchHorizontalMicro, SwitchHorizontalMini, SwitchHorizontalFull)
export const UserCircle: Glyph = glyph(UserCircleMicro, UserCircleMini, UserCircleFull)
export const Fingerprint: Glyph = glyph(FingerprintMicro, FingerprintMini, FingerprintFull)
export const MinusCircle: Glyph = glyph(MinusCircleMicro, MinusCircleMini, MinusCircleFull)

export const GitBranch: Glyph = DRAWN_GLYPHS.GitBranch
export const GitMerge: Glyph = DRAWN_GLYPHS.GitMerge
export const GitPullRequest: Glyph = DRAWN_GLYPHS.GitPullRequest
export const GitCommit: Glyph = DRAWN_GLYPHS.GitCommit
export const GitHub: Glyph = DRAWN_GLYPHS.GitHub
export const TreeStructure: Glyph = DRAWN_GLYPHS.TreeStructure
export const Quote: Glyph = DRAWN_GLYPHS.Quote

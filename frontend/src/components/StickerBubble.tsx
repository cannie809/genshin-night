interface StickerBubbleProps {
  stickerUrl: string
}

export function StickerBubble({ stickerUrl }: StickerBubbleProps) {
  return (
    <div
      className="animate-sticker-pop pointer-events-none absolute z-30 origin-bottom-left"
      style={{ bottom: 'calc(100% - 5px)', left: '40px' }}
    >
      {/* Bubble body */}
      <div className="bg-card/90 border-accent/25 relative rounded-2xl border p-2 shadow-[0_0_16px_rgba(124,58,237,0.2)] backdrop-blur-sm">
        <img
          src={stickerUrl}
          alt="sticker"
          className="size-14 object-contain drop-shadow-[0_0_4px_rgba(124,58,237,0.3)]"
          draggable={false}
        />
        {/* Tail — bottom-left corner, angled toward avatar (down-left) */}
        <div
          className="bg-card/90 border-accent/25 absolute border-b border-l"
          style={{
            width: '10px',
            height: '10px',
            bottom: '-2px',
            left: '2px',
            transform: 'rotate(-25deg) skewX(-10deg)',
          }}
        />
      </div>
    </div>
  )
}

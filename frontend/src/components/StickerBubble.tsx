interface StickerBubbleProps {
  stickerUrl: string;
}

export function StickerBubble({ stickerUrl }: StickerBubbleProps) {
  return (
    <div
      className="absolute z-30 animate-sticker-pop pointer-events-none origin-bottom-left"
      style={{ bottom: 'calc(100% - 5px)', left: '40px' }}
    >
      {/* Bubble body */}
      <div className="relative bg-card/90 backdrop-blur-sm rounded-2xl p-2 shadow-[0_0_16px_rgba(124,58,237,0.2)] border border-accent/25">
        <img
          src={stickerUrl}
          alt="sticker"
          className="size-14 object-contain drop-shadow-[0_0_4px_rgba(124,58,237,0.3)]"
          draggable={false}
        />
        {/* Tail — bottom-left corner, angled toward avatar (down-left) */}
        <div
          className="absolute bg-card/90 border-l border-b border-accent/25"
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
  );
}

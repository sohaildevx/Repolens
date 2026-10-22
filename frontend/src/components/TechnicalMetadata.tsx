interface TechnicalMetadataProps {
  items: string[]
  className?: string
}


export function TechnicalMetadata({
  items,
  className,
}: TechnicalMetadataProps) {
  return (
    <p
      className={
        'font-mono text-[11px] tracking-[0.18em] text-muted-foreground uppercase ' +
        (className ?? '')
      }
    >
      {items.map((item, index) => (
        <span key={item}>
          {index > 0 && (
            <span className="mx-3 text-primary/60" aria-hidden="true">
              /
            </span>
          )}
          {item}
        </span>
      ))}
    </p>
  )
}

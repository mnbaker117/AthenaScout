export function Spin() {
  return (
    <div
      style={{
        width: 16,
        height: 16,
        border: "2px solid transparent",
        borderTopColor: "currentColor",
        borderRadius: "50%",
        animation: "spin 0.6s linear infinite",
      }}
    />
  );
}

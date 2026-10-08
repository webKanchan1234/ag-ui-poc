import { Box, Button, Card, CardBody, CardFooter, CardHeader, Image, Select, Text } from "grommet";

interface ButtonSpec {
  label: string;
  value: unknown;
  variant?: "primary" | "default";
}

interface ButtonGroupSpec {
  type: "button-group";
  buttons: ButtonSpec[];
}

interface SelectSpec {
  type: "select";
  options: { label: string; value: unknown }[];
}

interface CardSpec {
  type: "card";
  title: string;
  description?: string;
  imageUrl?: string;
  actions?: ButtonSpec[];
}

interface CarouselSpec {
  type: "carousel";
  items: CardSpec[];
}

export type UiSpec = ButtonGroupSpec | SelectSpec | CardSpec | CarouselSpec;

// Title/description/actions all come from the tool call args, never hardcoded here.
function ToolCard({ spec, onRespond }: { spec: CardSpec; onRespond: (result: unknown) => void }) {
  return (
    <Card width="small" background="light-1" margin={{ right: "small" }}>
      {spec.imageUrl && <Image src={spec.imageUrl} fit="cover" />}
      <CardHeader pad="small">
        <Text weight="bold">{spec.title}</Text>
      </CardHeader>
      {spec.description && <CardBody pad="small">{spec.description}</CardBody>}
      {spec.actions && spec.actions.length > 0 && (
        <CardFooter pad="small" gap="small">
          {spec.actions.map((action, i) => (
            <Button
              key={i}
              primary={action.variant === "primary"}
              label={action.label}
              size="small"
              onClick={() => onRespond(action.value)}
            />
          ))}
        </CardFooter>
      )}
    </Card>
  );
}

// Renders whatever interactive widget the backend/LLM asked for, generically — adding a new
// tool never requires frontend changes as long as it reuses one of these widget shapes.
export function DynamicToolUI({ ui, onRespond }: { ui: UiSpec; onRespond: (result: unknown) => void }) {
  switch (ui.type) {
    case "button-group":
      return (
        <Box direction="row" gap="small" wrap>
          {ui.buttons.map((b, i) => (
            <Button
              key={i}
              primary={b.variant === "primary"}
              label={b.label}
              size="small"
              onClick={() => onRespond(b.value)}
            />
          ))}
        </Box>
      );
    case "select":
      return (
        <Select
          placeholder="Choose an option"
          options={ui.options}
          labelKey="label"
          valueKey="value"
          onChange={({ option }) => onRespond(option.value)}
        />
      );
    case "card":
      return <ToolCard spec={ui} onRespond={onRespond} />;
    case "carousel":
      return (
        <Box direction="row" gap="small" overflow={{ horizontal: "auto" }} pad={{ vertical: "xsmall" }}>
          {ui.items.map((item, i) => (
            <ToolCard key={i} spec={item} onRespond={onRespond} />
          ))}
        </Box>
      );
    default:
      return null;
  }
}

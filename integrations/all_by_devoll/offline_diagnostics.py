"""Evidence-limited offline diagnostic checklist for JALE.

This is a deterministic tool, not a generative language model. It never
invents prices, part numbers, fitment, diagnoses, or labor durations.
"""
import re

RULES = (
    (r"\b(brake|rotor|caliper|pad|abs|pedal|stopping)\b",
     ("Confirm the complaint, warning lights and when the symptom occurs",
      "Inspect brake-fluid level and visible leaks; check system condition",
      "Inspect relevant friction components and related hardware",
      "Verify the applicable vehicle repair information before work"),
     ("Brake components and hardware, subject to vehicle fitment",)),
    (r"\b(start|starter|battery|charging|alternator|crank|no.start|electrical)\b",
     ("Record battery condition and measured voltage",
      "Check relevant power, ground, fuses and connectors",
      "Differentiate no-crank, slow-crank and crank/no-start behavior",
      "Test the suspected circuit before replacing components"),
     ("Battery, terminals and charging/starting components if testing supports them",)),
    (r"\b(overheat|coolant|radiator|water.pump|thermostat|temperature)\b",
     ("Confirm the temperature complaint without running an overheating engine",
      "Check coolant level only under safe, cooled conditions",
      "Inspect the system for visible leaks",
      "Use manufacturer diagnostic procedures to isolate the cause"),
     ("Cooling system components, only after diagnosis",)),
    (r"\b(noise|vibration|suspension|steering|wheel|bearing|strut|alignment)\b",
     ("Record speed, terrain, load and steering conditions for the symptom",
      "Inspect relevant mounting points and exposed components",
      "Check for play or damage using appropriate vehicle procedures",
      "Confirm the precise source before ordering parts"),
     ("Suspension, steering or wheel components supported by inspection",)),
    (r"\b(scrap|junk|trash|haul|pickup|removal|dump|cleanout|debris)\b",
     ("Confirm quantity and type of materials to remove",
      "Check safe loading access, equipment and disposal requirements",
      "Confirm travel distance and disposal destination",
      "Record actual loading, transport and disposal time"),
     ("Loading supplies, protective equipment and disposal fees if applicable",)),
)

def draft(ticket):
    """Return a source-grounded, offline operator checklist for one job."""
    fields={k:str(ticket.get(k,"") or "")[:2000] for k in
            ("service","machine","problem","mobility","location")}
    text=" ".join(fields.values()).lower()
    checks=[]
    possible=[]
    categories=[]
    for pattern, steps, components in RULES:
        if re.search(pattern,text,re.I):
            checks.extend(steps)
            possible.extend(components)
            categories.append(pattern)
    if not checks:
        checks=["Confirm the customer's exact requested work",
                "Record the equipment or vehicle make, model, year and relevant symptoms",
                "Inspect the affected system before selecting work or replacement parts",
                "Verify applicable procedures and actual labor requirements"]
    checks=list(dict.fromkeys(checks))[:10]
    notes=["OFFLINE DIAGNOSTIC CHECKLIST — NOT A GENERATIVE AI ESTIMATE",
           "Customer request: "+(fields["problem"] or fields["service"] or "Details not provided"),
           "Operator checks:"]
    notes.extend("• "+step for step in checks)
    if possible:
        notes.append("Components to inspect (not confirmed parts):")
        notes.extend("• "+part for part in dict.fromkeys(possible))
    notes.extend(("Unknowns to verify: exact vehicle/equipment details, actual fault or work scope, "
                  "part fitment, on-site conditions and applicable labor time.",
                  "No prices, diagnosis or labor hours have been inferred."))
    return "\n".join(notes)[:10000]

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AddSourceForm from "../AddSourceForm.jsx";
import * as api from "../../api/client.js";

vi.mock("../../api/client.js");

describe("AddSourceForm validation", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a non-https URL and a malformed source id without calling the API", async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    render(<AddSourceForm knownPrograms={[]} onClose={() => {}} onCreated={onCreated} />);

    await user.type(screen.getByLabelText(/source id/i), "not-a-valid-id");
    await user.type(screen.getByLabelText(/country id/i), "AU");
    await user.type(screen.getByLabelText(/^program id$/i), "SKILLED_189");
    await user.type(screen.getByLabelText(/authority/i), "Home Affairs");
    await user.type(screen.getByLabelText(/^url$/i), "http://example.com");

    await user.click(screen.getByRole("button", { name: /create source/i }));

    expect(await screen.findByText(/must look like src-/i)).toBeInTheDocument();
    expect(await screen.findByText(/must be a valid https/i)).toBeInTheDocument();
    expect(api.createSource).not.toHaveBeenCalled();
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("submits when every field is valid", async () => {
    const user = userEvent.setup();
    api.createSource.mockResolvedValue({ source_id: "SRC-AU-TEST" });
    const onCreated = vi.fn();
    render(<AddSourceForm knownPrograms={[]} onClose={() => {}} onCreated={onCreated} />);

    await user.type(screen.getByLabelText(/source id/i), "SRC-AU-TEST");
    await user.type(screen.getByLabelText(/country id/i), "AU");
    await user.type(screen.getByLabelText(/^program id$/i), "SKILLED_189");
    await user.type(screen.getByLabelText(/authority/i), "Home Affairs");
    await user.type(screen.getByLabelText(/^url$/i), "https://example.gov.au/visa");

    await user.click(screen.getByRole("button", { name: /create source/i }));

    expect(api.createSource).toHaveBeenCalledWith(
      expect.objectContaining({ source_id: "SRC-AU-TEST", url: "https://example.gov.au/visa" })
    );
    expect(onCreated).toHaveBeenCalled();
  });
});

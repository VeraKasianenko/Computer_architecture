from machine.decoder import Decoder
from machine.machine_signals import Signal, Operands
from machine.isa import Opcode
from machine.logger import Logger

arithmetic_operations = [Opcode.ADD, Opcode.SUB, Opcode.MUL,
                         Opcode.DIV, Opcode.REM, Opcode.INC,
                         Opcode.DEC, Opcode.CMP, Opcode.MOVH]


class DataPath:

    def __init__(self, memory_capacity, ports):
        self.data_memory = [0] * memory_capacity
        self.ports = ports
        self.acc = 0
        self.buf_reg = 0
        self.stack_pointer = 0
        self.address_reg = 0
        self.memory_out = 0
        self.flags = {"z": False, "n": False}
        self.alu_out = 0
        self.in_interruption = False

    def signal_latch_acc(self, sel, load=0):
        self.acc = load if sel == Signal.DIRECT_ACC_LOAD else self.alu_out

    def signal_latch_address(self, sel, load=0):
        self.address_reg = load if sel == Signal.DIRECT_ADDRESS_LOAD else self.alu_out

    def memory_manager(self, operation):
        if operation == Signal.READ:
            self.memory_out = self.data_memory[self.address_reg]
        elif operation == Signal.WRITE:
            self.data_memory[self.address_reg] = self.alu_out

    def signal_latch_regs(self, *regs):
        if Signal.BUF_LATCH in regs:
            self.buf_reg = self.alu_out
        if Signal.STACK_LATCH in regs:
            self.stack_pointer = self.alu_out

    def execute_alu_operation(self, operation, value=0):
        match operation:
            case Opcode.ADD:
                return self.alu_out + value
            case Opcode.SUB:
                return self.alu_out - value
            case Opcode.MUL:
                return self.alu_out * value
            case Opcode.DIV:
                return self.alu_out // value
            case Opcode.REM:
                return self.alu_out % value
            case Opcode.INC:
                return self.alu_out + 1
            case Opcode.DEC:
                return self.alu_out - 1
            case Opcode.CMP:
                self.flags = {"z": self.alu_out == value, "n": self.alu_out < value}
                return self.alu_out
            case Opcode.MOVH:
                return self.alu_out << 24

    def get_bus_value(self, bus):
        match bus:
            case Operands.ACC:
                return self.acc
            case Operands.BUF:
                return self.buf_reg
            case Operands.STACK:
                return self.stack_pointer
            case Operands.MEM:
                return self.memory_out

    def alu_working(self, operation=Opcode.ADD, valves=[Operands.ACC]):
        self.alu_out = self.get_bus_value(valves[0])
        if Operands.ACC in valves:
            self.flags = {"z": self.acc == 0, "n": self.alu_out < 0}
        if operation in [Opcode.INC, Opcode.DEC, Opcode.MOVH]:
            self.alu_out = self.execute_alu_operation(operation)
        elif len(valves) > 1:
            self.alu_out = self.execute_alu_operation(operation, self.get_bus_value(valves[1]))


class ControlUnit:

    def __init__(self, code_file, instructions, data_path):
        self.ip = instructions[0]["_start"]
        del instructions[0]

        self.instructions = instructions
        self.data_path = data_path
        self.timer = self.Timer()
        self.log = Logger(code_file, "processor")
        self._tick = 0
        self.int_vector = 0
        self.instr_counter = 0
        self.ei = False
        self.int_rq = False

    def get_ticks(self):
        return self._tick

    def tick(self):
        self.log.debug(self, self._tick)

        self._tick += 1
        self.data_path.ports.add_input(self._tick)
        self.data_path.alu_out = 0
        self.data_path.memory_out = 0

        if self.ei:
            self.timer.time += 1
            if self.timer.time % self.timer.timer_delay == 0:
                self.int_rq = True

    def execute(self):
        while self.instructions[self.ip]["opcode"] != Opcode.HALT:
            self.instr = self.instructions[self.ip]
            self.instr_counter += 1
            decode = Decoder(self, self.instr["opcode"], self.instr["arg"] if "arg" in self.instr else 0)
            signal = Signal.NEXT_IP
            if not self.data_path.ports.input_tokens:
                pass

            if decode.opcode in [Opcode.LOAD, Opcode.STORE]:
                decode.decode_memory_commands()
            elif decode.opcode in arithmetic_operations:
                decode.decode_arithmetic_commands()
            elif decode.opcode in [Opcode.JMP, Opcode.JGE, Opcode.JE, Opcode.JNE]:
                signal = decode.decode_flow_commands()
            elif decode.opcode in [Opcode.CALL, Opcode.RET, Opcode.IRET, Opcode.FUNC]:
                decode.decode_subprogram_commands()
            elif decode.opcode in [Opcode.EI, Opcode.DI, Opcode.VECTOR, Opcode.TIMER]:
                decode.decode_interrupt_commands()
            elif decode.opcode in [Opcode.PUSH, Opcode.POP]:
                decode.decode_stack_commands()
            elif decode.opcode in [Opcode.IN, Opcode.OUT, Opcode.SIGN]:
                decode.decode_io_commands()

            if self.instr["opcode"] not in [Opcode.CALL, Opcode.IRET]:
                self.signal_latch_ip(signal, decode.arg)
            if self.int_rq:
                self.log.info("Interruption Request!", self._tick)
                decode.opcode = Opcode.ISR
                decode.decode_subprogram_commands()
        self.instr = self.instructions[self.ip]
        self.log.debug(self, self._tick)

        return "".join(self.data_path.ports.output_buffer), self.instr_counter, self._tick

    def signal_latch_ip(self, signal=Signal.NEXT_IP, arg=0):
        match signal:
            case Signal.NEXT_IP:
                self.ip += 1
            case Signal.JMP_ARG:
                self.ip = arg
            case Signal.DATA_IP:
                self.ip = self.data_path.alu_out

    def __repr__(self):
        dp = self.data_path
        return f"{'Interrupt! ' if dp.in_interruption else ''}" \
               f"[{self.instr['index']}: {self.instr['opcode']} {self.instr['arg'] if 'arg' in self.instr else ''}] - "\
               f"TICK: {self._tick} | ACC: {dp.acc} | BUF: {dp.buf_reg} | STACK: {dp.stack_pointer}" \
               f" | ADDR: {dp.address_reg} | ALU_OUT: {dp.alu_out} | MEM_OUT: {dp.memory_out}" \
               f" | FLAGS: {dp.flags} | EI: {self.ei} | INT_ADDRESS: {self.int_vector}" \
               f" | TIMER: {self.timer.timer_delay}"

    class Timer:
        def __init__(self):
            self.time = -2
            self.timer_delay = 0

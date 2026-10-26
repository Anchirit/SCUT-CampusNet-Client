/* Replace linux_get_device_network_info's unchecked address dereferences.
 * Linked at 0x080662d6; execution resumes at the original IPv4 handler.
 */
.section .text
.global _start
.set next_address, 0x08066412
.set handle_ipv4, 0x0806630c
_start:
    movl -0x18(%ebp), %eax
    movl 4(%eax), %eax
    testl %eax, %eax
    jz next_address
    cmpw $2, (%eax)
    jne next_address
    movl %eax, -0x14(%ebp)
    movl -0x18(%ebp), %eax
    movl 8(%eax), %eax
    testl %eax, %eax
    jz next_address
    movl %eax, -0x10(%ebp)
    jmp handle_ipv4
    .org 54, 0x90
